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

st.title("📝 Grit English 채점실행 명령 (1번/11번/20번 경계면 정밀 보정 엔진)")
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


def crop_middle_school_box(image: Image.Image, q_num: int):
    """
    중학 영단어 실력편 양식 기준 정밀 크롭 (1번, 11번, 20번 경계면 Y축 확장 보정)
    """
    width, height = image.size
    
    top_margin = height * 0.155
    mid_table_y = height * 0.525
    
    if 1 <= q_num <= 10:
        row_h = (mid_table_y - top_margin) / 10.0
        idx = q_num - 1
        x1, x2 = int(width * 0.28), int(width * 0.49)
        y1 = int(top_margin + (idx * row_h))
        y2 = int(top_margin + ((idx + 1) * row_h))
        
        # 💡 [1번 문항 최상단 보정]
        if q_num == 1:
            y1 = int(top_margin - (row_h * 0.15))
            
    elif 11 <= q_num <= 20:
        row_h = (mid_table_y - top_margin) / 10.0
        idx = q_num - 11
        x1, x2 = int(width * 0.74), int(width * 0.98)
        y1 = int(top_margin + (idx * row_h))
        y2 = int(top_margin + ((idx + 1) * row_h))
        
        # 💡 [11번 문항 우측 칼럼 시작점 보정]
        if q_num == 11:
            y1 = int(top_margin - (row_h * 0.15))
        # 💡 [20번 문항 최하단 보정]
        if q_num == 20:
            y2 = int(mid_table_y + (row_h * 0.15))
            
    elif 21 <= q_num <= 25:
        bot_top = height * 0.54
        bot_bot = height * 0.95
        sub_h = (bot_bot - bot_top) / 5.0
        idx = q_num - 21
        x1, x2 = int(width * 0.22), int(width * 0.98)
        y1 = int(bot_top + (idx * sub_h))
        y2 = int(bot_top + ((idx + 1) * sub_h))
    else:
        return image

    padding_x = int((x2 - x1) * 0.03)
    padding_y = int((y2 - y1) * 0.18)
    
    crop_box = (
        max(0, x1 - padding_x),
        max(0, y1 - padding_y),
        min(width, x2 + padding_x),
        min(height, y2 + padding_y)
    )
    return image.crop(crop_box)


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

start_grading = st.button("🚀 채점 시작 (1, 11, 20번 경계 보정 엔진)", type="primary", use_container_width=True)

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
                full_image = prepare_image(photo)

                system_role = (
                    "당신은 중학 영단어 실력편 시험지 전용 정밀 문자 판독(OCR) 시스템입니다.\n"
                    "주어진 단 하나의 문항 영역 이미지에서 학생이 직접 쓴 손글씨 알파벳을 철자 하나 다르게 바꾸지 않고 있는 그대로 타이핑하십시오.\n"
                    "1. 자의적 보정/유추/자동 교정은 철저히 금지됩니다. (필기체가 다소 흘려져 있어도 보이는 알파벳 그대로 읽을 것)\n"
                    "2. 답란이 비어있거나 공란인 경우 무조건 '(미응답)'으로 출력하십시오.\n"
                    "3. 취소선이나 기호가 섞여 있는 경우, 이를 제외하고 새로 고쳐 쓴 최종 알파벳 단어만 읽으십시오."
                )

                records = []
                for q_num_norm, c_ans in answer_dict.items():
                    if not q_num_norm.isdigit():
                        continue
                    
                    q_int = int(q_num_norm)
                    cropped_img = crop_middle_school_box(full_image, q_int)
                    img_b64 = image_to_base64(cropped_img)

                    prompt = (
                        "이 이미지는 중학 영단어 실력편 시험지의 {}번 문항 영역입니다.\n"
                        "여기에 적힌 학생의 손글씨 답안만 읽어서 출력하세요.\n"
                        "다른 설명 없이 오직 적힌 단어/문장만 출력하세요. (공란인 경우 '(미응답)' 출력)"
                    ).format(q_num_norm)

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
                                            "url": "data:image/jpeg;base64,{}".format(img_b64),
                                            "detail": "high"
                                        }
                                    }
                                ]
                            }
                        ]

                        response = client.chat.completions.create(
                            model=selected_model,
                            temperature=0.0,
                            messages=chat_messages
                        )

                        raw_written = response.choices[0].message.content.strip()
                        raw_written = raw_written.splitlines()[0].strip()

                        if raw_written in ["(미응답)", "미응답", "(공란)", "공란", "none", "null", ""]:
                            student_word = "(미응답)"
                            is_correct = False
                            reason = "미응답 (공란)"
                        else:
                            student_word = normalize_text(raw_written)
                            correct_word = normalize_text(str(c_ans))

                            if not is_english_text(student_word):
                                is_correct = False
                                reason = "영단어 미작성"
                            elif student_word == correct_word:
                                is_correct = True
                                reason = "정답"
                            else:
                                is_correct = False
                                reason = "철자 오류/오기 (작성: '{}' / 정답: '{}')".format(
                                    raw_written, str(c_ans).strip()
                                )

                    except Exception as item_err:
                        student_word = "(판독 오류)"
                        is_correct = False
                        reason = "API 판독 오류: {}".format(item_err)

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

                st.markdown("---")
