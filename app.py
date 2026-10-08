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
            "학생이 해당 문항 위치에 손글씨로 쓴 글자를 눈에 보이는 알파벳 '하나하나'를 하이픈(-)으로 나누어 적으세요. "
            "⚠️ [독립 판독 및 보정 절대 금지 명령]: 다른 문항이나 정답 맥락을 통해 단어를 미리 지레짐작하여 자체 보정하지 마세요. "
            "학생이 해당 문항에서 알파벳을 틀리게 썼거나 누락했다면(예: 'opportunity' 대신 'o-p-p-o-r-t-u-n-i-t'로 씀), "
            "실제 쓴 철자 그대로만 적어야 합니다. "
            "다만, 단어 끝글자(y, e, s, d, g, t 등)의 획이 작거나 희미하게 남아있다면 눈에 보이는 그대로 빠짐없이 하이픈으로 나누어 적어주세요. "
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
    text = text.lower
