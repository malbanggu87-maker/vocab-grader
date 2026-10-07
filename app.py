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

st.title("📝 Grit English 채점실행 명령")
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


def normalize_q_num(q_str: str) -> str:
    """문항 번호 정규화 (예: '1.', '1번', '01' -> '1')"""
    if not q_str:
        return ""
    q_clean = str(q_str).strip()
    q_clean = re.sub(r"\.0$", "", q_clean)
    q_clean = re.sub(r"[^\d]", "", q_clean)
    if q_clean.isdigit():
        return str(int(q_clean))
    return str(q_str).strip()


class QuestionResult(BaseModel):
    number: str = Field(
        description="시험지에 인쇄된 정확한 문항 번호 (예: '1', '18', '25')"
    )
    spelled_out_letters: str = Field(
        description=(
            "학생이 손글씨로 쓴 글자를 눈에 보이는 알파벳 '하나하나'를 하이픈(-)으로 나누어 적으세요. "
            "특히 'party'를 'part'로 쓰는 것처럼 단어 끝의 글자(y, e, s 등)나 중간 글자가 누락되는 경우를 철저히 감지하여 "
            "눈에 보이는 모든 알파벳을 빠짐없이 하이픈으로 연결해 적으세요. "
            "반복되는 알파벳('success'의 s-s, c-c 등)도 절대로 생략하지 마세요. 비어있거나 작성되지 않은 경우 '(미응답)'"
        )
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


def compress_and_encode_image(uploaded_file, max_size=3840):
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
    image.save(buffer, format="JPEG", quality=98, subsampling=0)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def clean_spelled_letters(raw_letters: str) -> str:
    """하이픈, 공백 등을 제거하고 순수 알파벳 토큰만 추출하여 완전한 철자 문자열 반환"""
    if not raw_letters:
        return ""
    text = unicodedata.normalize("NFC", str(raw_letters))
    text = text.lower().strip()
    tokens = re.findall(r"[a-z]", text)
    return "".join(tokens)


def normalize_text(text: str) -> str:
    """정답지 영단어 정규화 (알파벳 소문자만 연속 추출)"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.lower().strip()
    tokens = re.findall(r"[a-z]", text)
    return "".join(tokens)


def is_english_text(text: str) -> bool:
    return bool(re.search(r"[a-zA-Z]", text))


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

    raw_q_nums = df_sub.iloc[:, 0].astype(str)
    raw_q_ans = df_sub.iloc[:, 1].astype(str).str.strip()

    ans_dict = {}
    for q_n, q_a in zip(raw_q_nums, raw_q_ans):
        norm_key = normalize_q_num(q_n)
        if norm_key:
            ans_dict[norm_key] = q_a

    return ans_dict


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
                base64_image = compress_and_encode_image(photo, max_size=3840)

                prompt = (
                    "당신은 영단어 및 문장 시험지의 학생 손글씨를 한 글자도 빠짐없이 엄격하게 검증하는 수석 OCR 판독관입니다. "
                    "시험지에 각 문항의 맨 앞에 적힌 숫자(문항 번호)를 정확히 읽어내어 number 필드에 기록하고, "
                    "학생이 쓴 알파벳 하나하나를 하이픈(-)으로 나누어 spelled_out_letters에 작성하세요.\n\n"
                    "⚠️ [매우 중요 - 글자 누락 및 오탐 방지 규칙]\n"
                    "1. 'party'를 'part'로 쓰는 것처럼 단어 끝부분의 알파벳(예: y, e, s 등)이나 중간 알파벳이 누락되는 사례를 철저히 색출하세요. "
                    "학생이 쓰다 만 글자나 누락된 철자가 있다면 반드시 누락된 상태 그대로(예: p-a-r-t) 기록해야 합니다. 대충 비슷하다고 정답 처리하거나 임의로 글자를 채워 넣으면 절대 안 됩니다.\n"
                    "2. 'success', 'address'처럼 연속되는 알파벳 역시 개수를 정확히 세어 모두 하이픈으로 연결하세요.\n"
                    "3. 미응답 문항은 반드시 '(미응답)'으로 적어주세요."
                )

                response = client.beta.chat.completions.parse(
                    model=selected_model,
                    temperature=0.0,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high",
                                },
                                "type": "image_url",
                            },
                        ],
                    }],
                    response_format=GradingSchema,
                )

                parsed_data = response.choices[0].message.parsed
                gpt_parsed = {}
                if parsed_data and parsed_data.details:
                    for item in parsed_data.details:
                        norm_num = normalize_q_num(item.number)
                        gpt_parsed[norm_num] = str(
                            item.spelled_out_letters
                        ).strip()

                records = []
                for q_num_norm, c_ans in answer_dict.items():
                    if q_num_norm in gpt_parsed:
                        raw_spelled = gpt_parsed[q_num_norm]

                        if raw_spelled in [
                            "(미응답)",
                            "미응답",
                            "(공란)",
                            "공란",
                            "none",
                            "null",
                            "",
                        ]:
                            student_word = "(미응답)"
                            is_correct = False
                            reason = "미응답 (공란)"
                        else:
                            student_word = clean_spelled_letters(raw_spelled)
                            correct_word = normalize_text(str(c_ans))

                            if not is_english_text(student_word):
                                is_correct = False
                                reason = "영단어 미작성"
                            elif student_word == correct_word:
                                is_correct = True
                                reason = "정답"
                            else:
                                is_correct = False
                                reason = f"철자 불일치 또는 누락 (작성: '{student_word}' / 정답: '{correct_word}')"
                    else:
                        student_word = "(미응답)"
                        is_correct = False
                        reason = "시험지에서 해당 문항 번호를 찾지 못함 (미인식/미응답)"

                    records.append({
                        "문항 번호": q_num_norm,
                        "학생 작성 답안": student_word,
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
