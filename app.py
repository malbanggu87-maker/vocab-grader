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
    help="gpt-4o는 손글씨 및 철자 정밀 검증에 가장 우수한 성능을 보입니다.",
)


def extract_key_number(filename: str) -> str:
    """파일명에서 끝쪽 숫자 2자리를 추출 (숫자 1자리만 있는 경우 01 형태로 정규화)"""
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    numbers = re.findall(r"\d+", name_without_ext)
    if numbers:
        last_num = numbers[-1]
        return last_num.zfill(2)[-2:]
    return ""


# Structured Outputs용 Pydantic Schema 정의
class QuestionResult(BaseModel):
    number: str = Field(
        description="시험지에 인쇄된 정확한 문항 번호 (예: '1', '18', '25')"
    )
    student_answer: str = Field(
        description="학생이 손글씨로 쓴 알파벳을 '있는 그대로' 추출하세요. 정답과 비교했을 때 알파벳이 누락되었거나 틀렸다면, 절대 정답 단어로 보정하지 말고 학생이 실제로 쓴 오탈자 형태(예: 'encourge') 그대로 적어야 합니다. 비어있으면 '(공란)'"
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
    """소문자 변환 및 양쪽 공백만 제거 (철자는 단 1글자도 변형하지 않음)"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def is_english_text(text: str) -> bool:
    """답안에 알파벳이 포함되어 있는지 확인"""
    return bool(re.search(r"[a-zA-Z]", text))


def evaluate_answer(student_ans: str, correct_ans: str) -> tuple[bool, str]:
    """100% 엄격 철자 비교 함수 (알파벳 누락/오탈자 철저 검증)"""
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

                # 각 문항 번호와 실제 정답을 프롬프트에 구체적으로 제공하여 AI의 환각/자동완성 방지
                target_info_list = []
                for q_num, c_ans in answer_dict.items():
                    q_num_str = str(q_num).replace(".0", "").strip()
                    target_info_list.append(
                        f"문항 {q_num_str}: 정답은 '{str(c_ans).strip()}'"
                    )

                target_info_str = "\n".join(target_info_list)

                base64_image = compress_and_encode_image(photo, max_size=2400)

                prompt = f"""
                당신은 영단어 시험지의 학생 손글씨를 아주 매섭게 검증하는 엄격한 AI 채점관입니다.

                [참고: 각 문항의 교재 정답 데이터]
                {target_info_str}

                [철자 누락 및 오답 검출 절대 규칙 - 최우선 준수!]
                1. 학생이 쓴 손글씨와 위 [교재 정답 데이터]를 문항별로 엄격하게 비교하세요.
                2. 만약 학생이 알파벳을 빼먹었거나(예: 정답이 'encourage'인데 학생이 'encourge'로 쓴 경우), 오탈자가 있거나, 철자가 틀렸다면 **절대 교재 정답 단어로 자동 보정하거나 완성해서는 안 됩니다!**
                3. 반드시 학생이 실제 손글씨로 적은 결함 있는 글자 형태 그대로(예: 'encourge') `student_answer`에 추출해야 합니다. 정답대로 고쳐서 적으면 치명적인 채점 오류가 됩니다.
                4. 연필/펜 획이 전혀 없는 빈칸 영역은 무조건 **"(공란)"**으로 작성하세요.
                """

                response = client.beta.chat.completions.parse(
                    model=selected_model,
                    temperature=0.0,
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
