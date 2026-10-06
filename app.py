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

st.title("📝 제이슨의 구원방주")
st.write("Made by Jason")

st.sidebar.header("🔑 설정")

# Secrets에 저장된 API 키 확인
if "OPENAI_API_KEY" in st.secrets:
    api_key = st.secrets["OPENAI_API_KEY"]
    st.sidebar.success("✅ API 키가 저장되어 있습니다.")
else:
    api_key = st.sidebar.text_input("OpenAI API Key 입력", type="password")


def extract_key_number(filename: str) -> str:
    """파일명에서 끝쪽 숫자 2자리를 추출 (숫자 1자리만 있는 경우 01 형태로 정규화)"""
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    numbers = re.findall(r"\d+", name_without_ext)
    if numbers:
        last_num = numbers[-1]
        return last_num.zfill(2)[-2:]
    return ""


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


def compress_and_encode_image(uploaded_file, max_size=1200):
    """이미지 해상도 최적화, EXIF 회전 보정 및 RGB 변환"""
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
    image.save(buffer, format="JPEG", quality=85)
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


def evaluate_answer(student_ans: str, correct_ans: str) -> tuple[bool, str]:
    """정오답 판정 함수 (공란/미응답은 오답 처리)"""
    norm_student = normalize_text(student_ans)
    norm_correct = normalize_text(correct_ans)

    if not norm_student or norm_student in ["미응답", "(미응답)", "공란", "(공란)"]:
        return False, "미응답 (공란)"

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

            # 업로드된 각 사진 파일별로 순회하며 매칭되는 정답지를 찾아 채점
            for idx, photo in enumerate(student_photos):
                photo_key = extract_key_number(photo.name)

                st.markdown(
                    f"#### 📄 [{idx+1}/{len(student_photos)}] 파일명: `{photo.name}` (식별번호: `{photo_key}`)"
                )

                # 파일명 번호에 맞는 정답지 파일 찾기
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

                # 매칭된 정답지 파일 데이터 읽기
                answer_dict = load_answer_dict_from_file(matched_answer_file)
                formatted_answers = json.dumps(
                    answer_dict, ensure_ascii=False
                )

                base64_image = compress_and_encode_image(photo, max_size=1200)

                prompt = f"""
                당신은 영단어 및 문장 시험지를 채점하는 전문 채점 선생님입니다.

                [시험지 구조 및 문제 유형]
                1. [단어형]: 한글 뜻을 보고 이에 해당하는 영어 단어를 작성하는 문제
                2. [문장/구 빈칸형]: 한글 문장을 보고 영어 문장 중간의 빈칸(___)에 알맞은 영어 단어(동일 시험지 내에 나와 있는 영단어)를 채워 넣는 문제

                [채점 및 공란/미응답 인식 수칙 - 필독]
                - 교재 정답지에 있는 모든 문항 번호에 대해 빠짐없이 인식 결과를 작성해야 합니다.
                - 학생이 영단어를 써야 하는 란이 빈칸(공란)이거나, 답이 작성되어 있지 않은 경우 절대로 문항을 누락하지 말고 student_answer에 "(미응답)"으로 작성하세요.
                - 옅은 연필 자국 및 부분 훼손 글자도 흐릿하더라도 정답 의도가 명확하다면 판독하세요.
                - 글씨가 삐뚤빼뚤하거나 알파벳 획이 뭉개졌더라도 정답 의도가 명확하다면 인정하세요.
                - 완전히 검게 덧칠하거나 선을 긋고 새로 적은 경우 최종 답안을 최우선으로 인식하세요.

                [교재 정답지]
                {formatted_answers}

                [JSON 응답 형식]
                {{
                  "details": [
                    {{
                      "number": "문항번호",
                      "student_answer": "학생이 작성한 답 (공란인 경우 \"(미응답)\")",
                      "correct_answer": "교재 정답"
                    }}
                  ]
                }}
                """

                response = client.chat.completions.create(
                    model="gpt-4o",
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
                    response_format={"type": "json_object"},
                )

                result = json.loads(response.choices[0].message.content)
                details = result.get("details", [])

                # GPT 반환 결과를 문항 번호별 매핑 맵으로 변환
                gpt_parsed_answers = {}
                for detail in details:
                    q_num = str(detail.get("number", "")).replace(".0", "").strip()
                    if q_num:
                        gpt_parsed_answers[q_num] = str(
                            detail.get("student_answer", "")
                        ).strip()

                records = []
                # 교재 정답지(answer_dict)의 모든 문항을 기준으로 전수 검증 (공란 및 누락 완벽 감지)
                for q_num, c_ans in answer_dict.items():
                    q_num_str = str(q_num).replace(".0", "").strip()
                    raw_s_ans = gpt_parsed_answers.get(q_num_str, "(미응답)")

                    is_correct, reason = evaluate_answer(raw_s_ans, str(c_ans).strip())

                    # 미응답 또는 공란으로 판정된 경우 출력용 답안을 "(공란)"으로 통일
                    if not raw_s_ans or raw_s_ans in ["미응답", "(미응답)", "공란", "(공란)"]:
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

                # 해당 파일의 채점 통계 계산
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

                    # 화면 출력 (공란인 경우 '(공란)'으로 표시되어 오답으로 명확히 구분됨)
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
