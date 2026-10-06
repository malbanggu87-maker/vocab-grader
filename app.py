import base64
import io
import json
import re
import unicodedata
from PIL import Image, ImageOps
from openai import OpenAI
import pandas as pd
from pydantic import BaseModel, Field
import streamlit as st

st.set_page_config(
    page_title="영단어/문장 오답 전용 채점", page_icon="📝", layout="centered"
)

st.title("📝 제이슨의 구원방주")
st.write("Made by Jason")

st.sidebar.header("🔑 설정")

if "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]
    st.sidebar.success("✅ API 키가 저장되어 있습니다.")
else:
    api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

selected_model = st.sidebar.selectbox(
    "🤖 사용할 OpenAI 모델 선택",
    ["gpt-4o", "gpt-4o-mini"],
    index=0,
    help="gpt-4o는 손글씨 및 철자 인식 정밀도가 가장 우수한 모델입니다.",
)


def extract_key_number(filename: str) -> str:
    """파일명에서 끝쪽 숫자 2자리를 추출 (숫자 1자리만 있는 경우 01 형태로 정규화)"""
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    numbers = re.findall(r"\d+", name_without_ext)
    if numbers:
        last_num = numbers[-1]
        return last_num.zfill(2)[-2:]
    return ""


# Pydantic Schema 정의 (OpenAI Structured Outputs 용)
class QuestionResult(BaseModel):
    number: str = Field(
        description="시험지에 인쇄된 정확한 문항 번호 (예: '1', '11', '25')"
    )
    student_answer: str = Field(
        description="학생이 작성한 알파벳 그대로를 추출한 문자열. 오탈자나 미완성 단어가 있어도 절대로 올바른 단어로 수정하지 말고 눈에 보이는 알파벳 그대로 표기하세요. 공란이면 '(공란)' 작성."
    )


class GradingSchema(BaseModel):
    details: list[QuestionResult]


st.markdown("### 1단계: 학생 시험지 사진 업로드")
student_photos = st.file_uploader(
    "학생 시험지 사진 업로드 (다중 선택 가능)",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    key="photo_uploader",
)

st.markdown("### 2단계: 교재 정답지 엑셀 파일 업로드")
answer_files = st.file_uploader(
    "정답지 엑셀/CSV 파일 선택 (다중 선택 가능)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    key="answer_uploader",
)


def compress_and_encode_image(uploaded_file, max_size=2400):
    file_bytes = uploaded_file.getvalue()
    image = Image.open(io.BytesIO(file_bytes))

    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass

    if image.mode != "RGB":
        image = image.convert("RGB")

    image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def normalize_text(text: str) -> str:
    """소문자 변환 및 양쪽 공백만 제거 (철자는 단 1글자도 변형하거나 삭제하지 않음)"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.lower().strip()
    # 연속된 내부 공백만 1개로 축소
    text = re.sub(r"\s+", " ", text)
    return text


def is_english_text(text: str) -> bool:
    """답안에 알파벳이 포함되어 있는지 확인"""
    return bool(re.search(r"[a-zA-Z]", text))


def evaluate_answer(student_ans: str, correct_ans: str) -> tuple[bool, str]:
    """100% 엄격 철자 비교 함수"""
    raw_ans = student_ans.strip()

    # 1. 공란/미응답 감지
    if not raw_ans or raw_ans in [
        "(공란)",
        "공란",
        "미응답",
        "(미응답)",
        "none",
        "null",
    ]:
        return False, "미응답 (공란)"

    # 2. 영단어가 아닌 한글 뜻/기호 작성 시
    if not is_english_text(raw_ans):
        return False, "영단어 미작성"

    norm_student = normalize_text(raw_ans)
    norm_correct = normalize_text(correct_ans)

    # 3. 철자 완전 일치(Exact Match) 비교
    if norm_student == norm_correct:
        return True, "정답"
    else:
        return False, f"철자 불일치 (작성: '{raw_ans}' / 정답: '{correct_ans}')"


def load_answer_dict_from_file(ans_file) -> dict:
    answer_bytes = ans_file.getvalue()
    f_name = ans_file.name.lower()

    if f_name.endswith(".csv"):
        df_sub = pd.read_csv(io.BytesIO(answer_bytes), header=None).dropna(
            how="all"
        )
    else:
        df_sub = pd.read_excel(
            io.BytesIO(answer_bytes), header=None, engine="openpyxl"
        ).dropna(how="all")

    q_nums = (
        df_sub.iloc[:, 0]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )
    q_ans = df_sub.iloc[:, 1].astype(str).str.strip()

    return dict(zip(q_nums, q_ans))


answer_file_map = {}
if answer_files:
    for ans_f in answer_files:
        a_key = extract_key_number(ans_f.name)
        if a_key:
            answer_file_map[a_key] = ans_f

st.markdown("---")
if st.button("🚀 채점 시작", type="primary", use_container_width=True):
    if not api_key or not answer_files or not student_photos:
        st.error(
            "API 키, 학생 시험지 사진, 교재 정답지 엑셀 파일을 모두 업로드해 주세요."
        )
    else:
        try:
            client = OpenAI(api_key=api_key)
            st.markdown("### 📊 채점 결과")

            for idx, photo in enumerate(student_photos):
                photo_key = extract_key_number(photo.name)

                st.markdown(
                    f"#### 📄 [{idx+1}/{len(student_photos)}] 파일명: `{photo.name}` (번호: `{photo_key}`)"
                )

                matched_answer_file = answer_file_map.get(photo_key)
                if not matched_answer_file:
                    st.error(
                        f"❌ `{photo.name}`에 매칭되는 정답지(`{photo_key}`)를 찾지 못했습니다."
                    )
                    continue

                answer_dict = load_answer_dict_from_file(matched_answer_file)
                target_q_numbers = [
                    str(k).replace(".0", "").strip() for k in answer_dict.keys()
                ]

                base64_image = compress_and_encode_image(photo, max_size=2400)

                prompt = f"""
                당신은 영단어 시험지의 학생 손글씨를 정밀 OCR하는 판독관입니다.

                [검사 대상 문항 목록]
                {target_q_numbers}

                [철자 판독 절대 규칙 - 매우 중요]
                1. 학생이 쓴 글씨를 추측하거나 올바른 단어로 **자동 보정(Auto-complete)하지 마십시오.**
                   - 예: 정답이 'unexpected'이더라도 학생이 'unexpect'라고만 적었으면 **반드시 'unexpect'로만 추출**해야 합니다.
                   - 철자 하나, 알파벳 어미(ed, s, ing 등)가 빠지거나 틀린 경우에도 이미지에 적힌 알파벳 그대로 기록하세요.
                2. 문항 번호와 작성 칸을 정확히 1:1 매칭하세요.
                   - 답안란에 글씨가 전혀 없이 깨끗하게 비어있는 공란은 무조건 "(공란)"으로 입력하세요.
                """

                response = client.beta.chat.completions.parse(
                    model=selected_model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high",
                                },
                            },
                        ],
                    }],
                    response_format=GradingSchema,
                )

                parsed_data = response.choices[0].message.parsed
                gpt_parsed_answers = {}
                if parsed_data and parsed_data.details:
                    for item in parsed_data.details:
                        q_num = str(item.number).replace(".0", "").strip()
                        gpt_parsed_answers[q_num] = str(
                            item.student_answer
                        ).strip()

                records = []
                for q_num, c_ans in answer_dict.items():
                    q_num_str = str(q_num).replace(".0", "").strip()
                    raw_s_ans = gpt_parsed_answers.get(q_num_str, "(공란)")

                    is_correct, reason = evaluate_answer(
                        raw_s_ans, str(c_ans).strip()
                    )

                    if not raw_s_ans or raw_s_ans in [
                        "(공란)",
                        "공란",
                        "미응답",
                        "(미응답)",
                        "none",
                        "null",
                    ]:
                        display_s_ans = "(공란)"
                    else:
                        display_s_ans = raw_s_ans

                    records.append({
                        "문항 번호": q_num_str,
                        "학생 작성 답안": display_s_ans,
                        "교재 정답": str(c_ans).strip(),
                        "정오답": is_correct,
                        "사유": reason,
                    })

                total_q_count = len(records)
                total_correct = sum(1 for r in records if r["정오답"])
                wrong_count = total_q_count - total_correct

                wrong_details = [
                    {
                        "문항 번호": r["문항 번호"],
                        "학생 작성 답안": r["학생 작성 답안"],
                        "교재 정답": r["교재 정답"],
                        "사유": r["사유"],
                    }
                    for r in records
                    if not r["정오답"]
                ]

                st.info(
                    f"결과: **{total_correct} / {total_q_count}점** (틀린 문항: {wrong_count}개)"
                )

                if wrong_details:
                    df_wrong = pd.DataFrame(wrong_details)
                    st.dataframe(
                        df_wrong, use_container_width=True, hide_index=True
                    )

                    csv_data = df_wrong.to_csv(index=False).encode("utf-8-sig")
                    safe_filename = re.sub(r"[^\w\-_.]", "_", photo.name)
                    st.download_button(
                        label=f"📥 `{photo.name}` 오답노트 다운로드 (CSV)",
                        data=csv_data,
                        file_name=f"오답노트_{safe_filename}.csv",
                        mime="text/csv",
                        key=f"dl_{idx}",
                    )
                else:
                    st.balloons()
                    st.success("🎉 모든 문항을 맞혔습니다!")

                st.markdown("---")

        except Exception as e:
            st.error(f"채점 중 오류가 발생했습니다: {e}")
