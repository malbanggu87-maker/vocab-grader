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
        description="시험지에 인쇄된 정확한 문항 번호 (예: '1', '2', '14', '25')"
    )
    spelled_out_letters: str = Field(
        description=(
            "학생이 해당 문항 위치에 손글씨로 쓴 글자를 눈에 보이는 그대로 하이픈(-)으로 나누어 적으세요. "
            "예시: 'p-u-b-l-i-c' 또는 'p-i-l-e'\n"
            "⚠️ [절대 주의 - 오탈자 자동 보정 엄금 및 공란 처리]:\n"
            "1. 학생이 'pleasent', 'polloute', 'abandom', 'issontial' 등과 같이 철자를 조금이라도 틀리게 적었다면, 교재 정답('pleasant', 'pollute', 'abandon', 'essential')과 유사하다는 이유로 절대 임의로 고쳐서 인식하지 마십시오.\n"
            "2. 눈에 보이는 철자 그대로(예: 'p-l-e-a-s-e-n-t'가 아닌 'p-l-e-a-s-e-n-t'의 틀린 철자나 형태 등) 추출해야 하며, 철자가 틀렸으므로 반드시 오답 처리되도록 정확한 스펠링을 기록하세요.\n"
            "3. 답란이 비어있는 경우 반드시 '(미응답)'으로 작성하세요."
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
    """손글씨 인식 시 명백하게 판독된 알파벳 오인식에 대해서만 제한적으로 보정 (오답 자체를 정답으로 만들지 않음)"""
    # pleasent, polloute 등의 오탈자는 절대 보정하지 않고 그대로 유지하여 오답 처리되도록 함
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
            st.error("OpenAI 클라이언트 초기화 오류: {}".format(e))
            client = None

        if client:
            st.markdown("### 📊 채점 결과")

            for idx, photo in enumerate(student_photos):
                photo_key = extract_key_number(photo.name)

                st.markdown("#### 📄 [{}/{}] 파일명: `{}` (번호: `{}`)".format(
                    idx + 1, len(student_photos), photo.name, photo_key
                ))

                matched_answer_file = answer_file_map.get(photo_key)
                if not matched_answer_file:
                    st.error("❌ `{}`에 매칭되는 정답지(`{}`)를 찾지 못했습니다.".format(
                        photo.name, photo_key
                    ))
                    continue

                answer_dict = load_answer_dict_from_file(matched_answer_file)
                base64_image = compress_and_encode_image(photo, max_size=3840)

                answer_context_str = "\n".join(
                    ["- {}번 정답 기준: {}".format(k, v) for k, v in answer_dict.items()]
                )

                prompt = "당신은 영단어 및 문장 시험지의 학생 손글씨를 철저히 검증하는 초정밀 OCR 판독관입니다.\n\n📋 [참고용 해당 시험지 문항별 정답 리스트]:\n{}\n\n🚨 [오탈자 자동 보정 금지 및 공란 철저 검증 규칙]\n1. [오탈자 임의 보정 절대 금지 (가장 중요)]: \n    - 학생이 'pleasent', 'polloute', 'abandom', 'issontial' 등 철자를 틀리게 적었을 때, 교재 정답('pleasant', 'pollute', 'abandon', 'essential')과 비슷하다는 이유로 임의로 교정하여 정답으로 처리하는 행위를 절대 금지합니다.\n    - 학생이 적은 철자 그대로(예: 'p-l-e-a-s-e-n-t' 등 눈에 보이는 오탈자) 정직하게 추출해야 오답으로 정확히 판정됩니다.\n2. [공란(미응답) 절대 누락 금지]:\n    - 답란이 비어있는 경우 반드시 '(미응답)'으로 출력하세요.".format(answer_context_str)

                try:
                    chat_messages = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/jpeg;base64,{}".format(base64_image),
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
                                
                                student_word = correct_similar_spellings(student_word, correct_word)

                                if not is_english_text(student_word):
                                    is_correct = False
                                    reason = "영단어 미작성"
                                elif student_word == correct_word:
                                    is_correct = True
                                    reason = "정답"
                                else:
                                    is_correct = False
                                    reason = "철자 불일치/누락 (작성: '{}' / 정답: '{}')".format(
                                        student_word, correct_word
                                    )
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

                    st.info("결과: **{} / {}점** (틀린 문항: {}개)".format(
                        total_correct, total_q_count, wrong_count
                    ))

                    if wrong_details:
                        df_wrong = pd.DataFrame(wrong_details)
                        st.dataframe(df_wrong, use_container_width=True, hide_index=True)

                        csv_data = df_wrong.to_csv(index=False).encode("utf-8-sig")
                        safe_filename = re.sub(r"[^\w\-_.]", "_", photo.name)
                        st.download_button(
                            label="📥 `{}` 오답노트 다운로드 (CSV)".format(photo.name),
                            data=csv_data,
                            file_name="오답노트_{}.csv".format(safe_filename),
                            mime="text/csv",
                            key="dl_{}".format(idx),
                        )
                    else:
                        st.balloons()
                        st.success("🎉 모든 문항을 맞혔습니다!")

                except Exception as api_err:
                    st.error("API 호출 또는 채점 처리 중 오류 발생: {}".format(api_err))

                st.markdown("---")
