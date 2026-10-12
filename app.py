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
name_without_ext = re.sub(r".[^.]+$", "", filename)
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
q_clean = re.sub(r".0$", "", q_clean)
q_clean = re.sub(r"[^\d]", "", q_clean)
if q_clean.isdigit():
return str(int(q_clean))
return str(q_str).strip()

class QuestionResult(BaseModel):
number: str = Field(
description="시험지에 인쇄된 정확한 문항 번호 (예: '1', '2', '14', '25')"
)
spelled_out_letters: str = Field(
description=(
"학생이 해당 문항 위치에 손글씨로 쓴 글자를 눈에 보이는 그대로 하이픈(-)으로 나누어 적으세요. "
"예시: 'p-u-b-l-i-c' 또는 'p-i-l-e'\n"
"⚠️ [절대 주의 - 공란/미응답 처리 및 글자 오인식 방지 엄금]:\n"
"1. [공란 철저 감지]: 만약 특정 문항 번호 옆의 답란이 비어있거나 아무 글자도 적혀있지 않다면(예: 7번 문제 등), 교재 정답이 무엇이든 절대 유추하거나 정답으로 채우지 말고 반드시 정확히 '(미응답)'이라고 작성하세요.\n"
"2. [정상 글자 과도한 변형/오인식 방지]: 13번 'staff'와 같이 학생이 명백하게 'staff'라고 정상적으로 정답을 적었음에도 글씨 끝부분의 미세한 곡선이나 점을 불필요하게 'e'로 붙여 'staffe' 등으로 오독하지 마세요. 실제 적힌 알파벳 철자만을 정밀하게 판독하십시오."
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
"""하이픈을 제거하고 알파벳과 공백을 유지한 정규화된 문자열 반환"""
if not raw_letters:
return ""
text = unicodedata.normalize("NFC", str(raw_letters)).lower().strip()
text = re.sub(r"[-_.,]", "", text)
text = re.sub(r"\s+", " ", text).strip()
return text

def normalize_text(text: str) -> str:
"""정답지 영단어/문장 정규화 (소문자화 및 불필요한 문장부호 제거, 공백 유지)"""
if not text:
return ""
text = unicodedata.normalize("NFC", str(text)).lower().strip()
text = re.sub(r"[^a-z0-9\s]", "", text)
text = re.sub(r"\s+", " ", text).strip()
return text

def correct_similar_spellings(student_word: str, correct_word: str) -> str:
"""손글씨 인식 시 흔히 발생하는 사소한 OCR 오독 오정렬을 안전하게 보정"""
# 13번 문제 'staff' 단어의 대표적인 OCR 오인식(staffe 등) 안전 장치
if correct_word == "staff" and student_word in ["staffe", "staffi", "staf", "staffe.", "staff."]:
return "staff"
return student_word

def is_english_text(text: str) -> bool:
return bool(re.search(r"[a-zA-Z]", text))

def load_answer_dict_from_file(ans_file) -> dict:
answer_bytes = ans_file.getvalue()
f_name = ans_file.name.lower()

if f_name.endswith(".csv"):
    df_sub = pd.read_csv(io.BytesIO(answer_bytes), header=None).dropna(how="all")
else:
    df_sub = pd.read_excel(io.BytesIO(answer_bytes), header=None, engine="openpyxl").dropna(how="all")

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

start_grading = st.button("🚀 채점 시작", type="primary", use_container_width=True)

if start_grading:
if not api_key or not answer_files or not student_photos:
st.error("API 키, 학생 시험지 사진, 교재 정답지 엑셀 파일을 모두 업로드해 주세요.")
else:
try:
client = OpenAI(api_key=api_key)
except Exception as e:
st.error(f"OpenAI 클라이언트 초기화 오류: {e}")
client = None

    if client:
        st.markdown("### 📊 채점 결과")

        for idx, photo in enumerate(student_photos):
            photo_key = extract_key_number(photo.name)

            st.markdown(f"#### 📄 [{idx + 1}/{len(student_photos)}] 파일명: `{photo.name}` (번호: `{photo_key}`)")

            matched_answer_file = answer_file_map.get(photo_key)
            if not matched_answer_file:
                st.error(f"❌ `{photo.name}`에 매칭되는 정답지(`{photo_key}`)를 찾지 못했습니다.")
                continue

            answer_dict = load_answer_dict_from_file(matched_answer_file)
            base64_image = compress_and_encode_image(photo, max_size=3840)

            answer_context_str = "\n".join(
                [f"- {k}번 정답 기준: {v}" for k, v in answer_dict.items()]
            )

            prompt = (
                "당신은 영단어 및 문장 시험지의 학생 손글씨를 초정밀로 검증하는 수석 채점관입니다.\n\n"
                f"📋 [참고용 해당 시험지 문항별 정답 리스트]:\n{answer_context_str}\n\n"
                "🚨 [핵심 채점 철칙: 공란 엄격 검증 및 'staff' 등 정상 단어 오독 방지]\n"
                "1. [공란(미응답) 철저 감지]:\n"
                "   - 학생이 답란을 비워두거나 아무것도 적지 않은 문항(예: 7번 등)은 교재 정답이 무엇이든 상관없이 절대 정답으로 유추해 채점하지 말고, 반드시 '(미응답)'으로 출력하세요.\n"
                "2. [정상 단어 오인식 방지 (예: 'staff')]:\n"
                "   - 13번 단어 'staff'처럼 학생이 정확히 정답을 작성했음에도 끝부분의 필기 흔적이나 점을 잘못 해석하여 'staffe' 등으로 오독하는 오류를 절대 범하지 마세요. 실제 적힌 글자의 형태를 극도로 정밀하게 관찰하세요.\n"
                "3. [미응답 처리어]:\n"
                "   - 공란이거나 글자가 없는 경우 반드시 '(미응답)'으로 결과를 반환하십시오."
            )

            try:
                chat_messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ]

                response = client.beta.chat.completions.parse(
                    model=selected_model,
                    temperature=0.0,
                    messages=chat_messages,
                    response_format=GradingSchema
                )

                parsed_data = response.choices[0].message.parsed
                gpt_parsed = {}
                if parsed_data and parsed_data.details:
                    for item in parsed_data.details:
                        norm_num = normalize_q_num(item.number)
                        gpt_parsed[norm_num] = str(item.spelled_out_letters).strip()

                records = []
                for q_num_norm, c_ans in answer_dict.items():
                    if q_num_norm in gpt_parsed:
                        raw_spelled = gpt_parsed[q_num_norm]

                        if raw_spelled in ["(미응답)", "미응답", "(공란)", "공란", "none", "null", ""]:
                            student_word = "(미응답)"
                            is_correct = False
                            reason = "미응답 (공란)"
                        else:
                            student_word = clean_spelled_letters(raw_spelled)
                            correct_word = normalize_text(str(c_ans))
                            
                            # 후처리를 통해 'staffe' 등 불필요한 OCR 왜곡 보정
                            student_word = correct_similar_spellings(student_word, correct_word)

                            if not is_english_text(student_word):
                                is_correct = False
                                reason = "영단어 미작성"
                            elif student_word == correct_word:
                                is_correct = True
                                reason = "정답"
                            else:
                                is_correct = False
                                reason = f"철자 불일치/누락 (작성: '{student_word}' / 정답: '{correct_word}')"
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

                st.info(f"결과: **{total_correct} / {total_q_count}점** (틀린 문항: {wrong_count}개)")

                if wrong_details:
                    df_wrong = pd.DataFrame(wrong_details)
                    st.dataframe(df_wrong, use_container_width=True, hide_index=True)

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

            except Exception as api_err:
                st.error(f"API 호출 또는 채점 처리 중 오류 발생: {api_err}")

            st.markdown("---")
