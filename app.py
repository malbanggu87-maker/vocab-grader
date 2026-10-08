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
        description="시험지에 인쇄된 정확한 문항 번호 (예: '1', '8', '18', '25')"
    )
    spelled_out_letters: str = Field(
        description=(
            "학생이 해당 문항 위치에 손글씨로 쓴 글자를 눈에 보이는 알파벳 '하나하나'를 하이픈(-)으로 나누어 적으세요. "
            "동일한 정답 단어가 여러 문항에 나오더라도 반드시 각 문항에 적힌 글자만 개별적으로 읽어야 합니다. "
            "특히 필기체 특성상 흘려 쓰거나 작게 쓰인 소문자 'r', 'n', 'm', 'u', 'i', 'l', 'e', 's' 및 단어 끝 글자(y, e, s 등)가 "
            "누락되지 않도록 획의 연결 부위를 정밀하게 확인하고 작성하세요. "
            "단, 실제로 학생이 철자를 틀렸거나 누락(예: 'party'를 'part'로 작성)한 경우에는 그 오탈자를 있는 그대로 하이픈으로 적으세요. "
            "비어있거나 작성되지 않은 경우 '(미응답)'"
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
                    "당신은 영단어 및 문장 시험지의 학생 손글씨를 한 글자도 빠짐없이 엄격하게 검증하는 초정밀 OCR 판독관입니다.\n\n"
                    "시험지의 각 문항 번호를 정확히 인식하여 number 필드에 기록하고, 학생이 작성한 알파벳 손글씨를 한 글자씩 하이픈(-)으로 연결하여 spelled_out_letters 필드에 판독하세요.\n\n"
                    "🔍 [손글씨 필기체 인식 핵심 주의사항 - 'r' 및 미세 획 판독]:\n"
                    "1. 학생들이 손글씨로 쓴 알파벳 중 소문자 'r'은 단순 꺾임이나 작은 물결 모양으로 짧게 표기되는 경우가 많습니다. 'surprising', 'create', 'creative', 'comfort', 'person' 등의 단어에서 'r' 획을 무심코 생략하거나 건너뛰지 말고 꼼꼼히 확인하세요.\n"
                    "2. 'r', 'n', 'm', 'u', 'w', 'v', 'l', 'i' 등 필기체에서 획이 겹치거나 가늘게 표현된 철자도 문맥과 필적을 정밀 분석하여 정확하게 알파벳을 읽어내야 합니다.\n"
                    "3. 동일한 영단어가 여러 문항에 사용되더라도 타 문항의 결과를 복사하지 말고 해당 위치의 손글씨만 독립적으로 읽으세요.\n"
