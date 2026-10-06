import base64
import io
import json
import re
import unicodedata
from PIL import Image, ImageOps
from openai import OpenAI
import pandas as pd

# Pydantic을 이용한 Strict JSON Response 구조 정의 (OpenAI 강제 응답용)
from pydantic import BaseModel, Field
import streamlit as st

st.set_page_config(
    page_title="영단어/문장 오답 전용 채점", page_icon="📝", layout="centered"
)

st.title("📝 제이슨의 구원방주")
st.write("Made by Jason")

st.sidebar.header("🔑 설정")

# Secrets에 저장된 API 키 확인
if "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]
    st.sidebar.success("✅ API 키가 저장되어 있습니다.")
else:
    api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")

selected_model = st.sidebar.selectbox(
    "🤖 사용할 OpenAI 모델 선택",
    ["gpt-4o", "gpt-4o-mini"],
    index=0,
    help="gpt-4o는 손글씨 및 공란 감지 능력이 가장 우수한 플래그십 모델입니다.",
)


def extract_key_number(filename: str) -> str:
    """파일명에서 끝쪽 숫자 2자리를 추출 (숫자 1자리만 있는 경우 01 형태로 정규화)"""
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    numbers = re.findall(r"\d+", name_without_ext)
    if numbers:
        last_num = numbers[-1]
        return last_num.zfill(2)[-2:]
    return ""


# ---------------------------------------------------------
# Pydantic Schema 정의 (OpenAI Structured Outputs 용)
# ---------------------------------------------------------
class QuestionResult(BaseModel):
    number: str = Field(description="문항 번호 (예: '1', '2', '01')")
    student_answer: str = Field(
        description="학생이 답안란/빈칸에 쓴 영어 단어. 만약 답안란에 아무것도 적혀있지 않고 완전히 비어있다면 반드시 '(공란)'이라고 명시하세요."
    )


class GradingSchema(BaseModel):
    details: list[QuestionResult]


# ---------------------------------------------------------
# 1단계: 학생 시험지 사진 업로드 (다중 선택 가능)
# ---------------------------------------------------------
st.markdown("### 1단계: 학생 시험지 사진 업로드")
st.caption("📌 파일명 끝 2자리 숫자(예: `시험지_01.jpg`)와 정답지 숫자가 매칭됩니다.")

student_photos = st.file_uploader(
    "학생 시험지 사진 업로드 (다중 선택 가능)",
    type=["jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
    key="photo_uploader",
)

# ---------------------------------------------------------
# 2단계: 교재 정답지 엑셀/CSV 파일 업로드 (다중 선택 가능)
# ---------------------------------------------------------
st.markdown("### 2단계: 교재 정답지 엑셀 파일 업로드")
st.info("💡 파일명 끝 2자리 숫자(예: `정답지_01.xlsx`)가 시험지와 일치하는 정답지를 자동으로 찾아 채점합니다.")

answer_files = st.file_uploader(
    "정답지 엑셀/CSV 파일 선택 (다중 선택 가능)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    key="answer_uploader",
)


def compress_and_encode_image(uploaded_file, max_size=2400):
    """이미지 해상도를 2400px 수준으로 유지하여 미세 손글씨 및 비어있는 빈칸 선명도 확보"""
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


def normalize_text(text: str, remove_punctuation: bool = True) -> str:
    """텍스트 정규화 (대소문자, 공백, 문장부호)"""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = text.lower()
    if remove_punctuation:
        text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_english_text(text: str) -> bool:
    """답안에 알파벳(영어 단어)이 포함되어 있는지 확인"""
    return bool(re.search(r"[a-zA-Z]", text))


def evaluate_answer(student_ans: str, correct_ans: str) -> tuple[bool, str]:
    """정오답 판정 함수"""
    raw_ans = student_ans.strip()

    # 공란/미응답 감지
    if not raw_ans or raw_ans in ["(공란)", "공란", "미응답", "(미응답)", "none", "null"]:
        return False, "미응답 (공란)"

    # 영단어가 아닌 한글 뜻이나 이상 기호를 적은 경우
    if not is_english_text(raw_ans):
        return False, "영단어가 아닌 답안 작성 (한글/기타)"

    norm_student = normalize_text(raw_ans)
    norm_correct = normalize_text(correct_ans)

    if norm_student == norm_correct:
        return True, "정답"
    else:
        return False, "철자 불일치"


def load_answer_dict_from_file(ans_file) -> dict:
    """단일 정답지 파일(엑셀/CSV)에서 정답 데이터 딕셔너리 추출"""
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


# ---------------------------------------------------------
# 업로드된 사진 및 정답지 파일 상태 요약 및 매칭 확인
# ---------------------------------------------------------
if student_photos:
    st.success(
        f"📸 총 {len(student_photos)}장의 시험지 사진이 업로드되었습니다."
    )

    with st.expander("🔍 업로드한 시험지 원본 사진 미리보기"):
        cols = st.columns(3)
        for idx, photo in enumerate(student_photos):
            p_key = extract_key_number(photo.name)
            with cols[idx % 3]:
                try:
                    photo_bytes = photo.getvalue()
                    img = Image.open(io.BytesIO(photo_bytes))
                    img = ImageOps.exif_transpose(img)
                    st.image(
                        img,
                        caption=f"파일명: {photo.name} (식별번호: {p_key if p_key else '없음'})",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.warning(f"이미지 미리보기 실패 ({photo.name}): {e}")

# 정답지 파일 딕셔너리 맵 생성 (식별 번호 -> 정답지 파일)
answer_file_map = {}
if answer_files:
    for ans_f in answer_files:
        a_key = extract_key_number(ans_f.name)
        if a_key:
            answer_file_map[a_key] = ans_f

    file_names = ", ".join([f"`{f.name}`" for f in answer_files])
    st.info(
        f"📊 총 {len(answer_files)}개의 정답지 파일이 불러와졌습니다: {file_names}"
    )


# ---------------------------------------------------------
# 채점 실행 버튼 영역
# ---------------------------------------------------------
st.markdown("---")
if st.button("🚀 채점을 조지십시요", type="primary", use_container_width=True):
    if not api_key or not answer_files or not student_photos:
        st.error(
            "API 키, 학생 시험지 사진, 교재 정답지 엑셀 파일을 모두 업로드해 주세요."
        )
    else:
        try:
            client = OpenAI(api_key=api_key)
            st.markdown("### 📊 파일별 채점 결과")

            for idx, photo in enumerate(student_photos):
                photo_key = extract_key_number(photo.name)

                st.markdown(
                    f"#### 📄 [{idx+1}/{len(student_photos)}] 파일명: `{photo.name}` (식별번호: `{photo_key}`)"
                )

                matched_answer_file = answer_file_map.get(photo_key)

                if not matched_answer_file:
                    st.error(
                        f"❌ `{photo.name}`에 맞는 정답지(끝 2자리 `{photo_key}`)를 찾을 수 없습니다. 건너뜁니다."
                    )
                    st.markdown("---")
                    continue

                st.caption(
                    f"🔗 매칭된 정답지: `{matched_answer_file.name}`"
                )

                answer_dict = load_answer_dict_from_file(matched_answer_file)
                target_q_numbers = [str(k).replace(".0", "").strip() for k in answer_dict.keys()]

                # 고해상도 이미지 변환 (2400px)
                base64_image = compress_and_encode_image(photo, max_size=2400)

                prompt = f"""
                당신은 영단어/영문장 시험지의 학생 답안을 검사하는 정밀 OCR 채점관입니다.

                [검사 대상 문항 목록]
                반드시 아래 문항 번호들에 대해 빠짐없이 하나씩 답안 영역을 정밀하게 점검하세요:
                {target_q_numbers}

                [공란(미응답) 검사 핵심 수칙 - 필독]
                1. 각 문항의 번호 옆, 밑줄(___), 또는 답안 작성 칸의 '실제 종이 여백'을 정밀하게 확인하세요.
                2. 만약 해당 영역에 연필/펜으로 쓰인 연한 알파벳이나 필적이 전혀 없이 **완전히 깨끗한 백지 상태/공란**이라면,
                   절대로 임의로 답을 추측하지 말고 **student_answer에 "(공란)"**이라고 정확히 기록하세요.
                3. 문항 문제에 한글 뜻이 1개 이상 주어져 있더라도, 답안란에는 **학생이 작성한 '영어 단어'**가 있어야 합니다.
                   - 답안란이 비어있으면 -> "(공란)"
                   - 답안란에 글씨가 있으면 -> 보이는 영단어를 있는 그대로 추출
                4. 이미지 판독 중 일부 문항을 생략하거나 건너뛰지 마시고, 위 문항 목록 전체를 일대일로 100% 매칭하여 반환하세요.
                """

                # Structured Outputs (pydantic) 기법을 사용하여 Schema 준수 강제
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
                        gpt_parsed_answers[q_num] = str(item.student_answer).strip()

                records = []
                # 교재 정답지 기준 100% 전수 채점 (AI 응답에서 탈락된 문항이 있더라도 교재 정답지 목록으로 자동 보정)
                for q_num, c_ans in answer_dict.items():
                    q_num_str = str(q_num).replace(".0", "").strip()
                    raw_s_ans = gpt_parsed_answers.get(q_num_str, "(공란)")

                    is_correct, reason = evaluate_answer(raw_s_ans, str(c_ans).strip())

                    # 공란 오답 표시
                    if not raw_s_ans or raw_s_ans in ["(공란)", "공란", "미응답", "(미응답)", "none", "null"]:
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
                    }
                    for r in records
                    if not r["정오답"]
                ]

                st.info(
                    f"결과: **{total_correct} / {total_q_count}점** (틀린 문항: {wrong_count}개)"
                )

                if wrong_details:
                    df_wrong = pd.DataFrame(wrong_details)

                    # 오답 테이블 출력 ((공란) 표출 확인)
                    st.dataframe(
                        df_wrong,
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.write("")

                    # CSV 다운로드 파일 생성
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
