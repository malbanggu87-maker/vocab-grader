import base64
import io
import json
import re
import unicodedata
from PIL import Image, ImageOps
from openai import OpenAI
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="영단어/문장 오답 전용 채점", page_icon="📝", layout="centered"
)

st.title("📝 Grit English 채점실행 명령 (2단 영역 분할 & Strict OCR 모드)")
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


def prepare_image(uploaded_file):
    """이미지 EXIF 회전 보정 및 RGB 변환"""
    file_bytes = uploaded_file.getvalue()
    image = Image.open(io.BytesIO(file_bytes))
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    if image.mode != "RGB":
        image = image.convert("RGB")
    return image


def crop_columns(image: Image.Image):
    """시험지 이미지를 좌측 칼럼과 우측 칼럼으로 2단 분할"""
    width, height = image.size
    top = int(height * 0.18)
    bottom = int(height * 0.95)
    mid_x = int(width * 0.50)

    left_col = image.crop((0, top, mid_x, bottom))
    right_col = image.crop((mid_x, top, width, bottom))
    return left_col, right_col


def image_to_base64(pil_img: Image.Image) -> str:
    buffer = io.BytesIO()
    pil_img.save(buffer, format="JPEG", quality=95)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def normalize_text(text: str) -> str:
    """정답지 및 작성 답안 정규화"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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

start_grading = st.button("🚀 채점 시작 (전면 개편 엔진)", type="primary", use_container_width=True)

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
                image = prepare_image(photo)
                left_col_img, right_col_img = crop_columns(image)

                left_b64 = image_to_base64(left_col_img)
                right_b64 = image_to_base64(right_col_img)

                target_numbers = sorted([int(k) for k in answer_dict.keys() if k.isdigit()])
                target_numbers_str = ", ".join([str(n) for n in target_numbers])

                system_role = (
                    "당신은 정밀 문자 판독(OCR) 전용 시스템입니다.\n"
                    "주어진 영역별 이미지에서 학생이 직접 쓴 손글씨 알파벳을 철자 하나 다르게 바꾸지 않고 있는 그대로 타이핑하십시오.\n"
                    "1. 단어의 자의적 보정/유추/자동 교정은 철저히 금지됩니다. (예: 'hol'을 'hole'로, 'attribut'를 'attribute'로, 'verity'를 'variety'로, 'sentense'를 'sentence'로 고쳐 쓰지 말고 눈에 보이는 알파벳 그대로 적으시오)\n"
                    "2. 답란에 아무것도 쓰여있지 않거나 비어있는 공란은 무조건 '(미응답)'으로 출력하십시오.\n"
                    "3. 취소선이 그어진 단어는 무시하고, 새로 고쳐 쓴 최종 단어만 필사하십시오."
                )

                prompt = (
                    "두 개의 이미지(첫 번째: 좌측 문항 칼럼, 두 번째: 우측 문항 칼럼)에서 아래 문항 번호들에 해당되는 학생의 손글씨 답안을 읽으세요.\n\n"
                    "📌 [판독해야 할 문항 번호 목록]: {}\n\n"
                    "반드시 아래 형식으로만 출력하세요. 설명은 금지합니다.\n"
                    "1: fill\n"
                    "5: hol\n"
                    "10: sentense\n"
                    "18: opprove"
                ).format(target_numbers_str)

                try:
                    chat_messages = [
                        {"role": "system", "content": system_role},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/jpeg;base64,{}".format(left_b64),
                                        "detail": "high"
