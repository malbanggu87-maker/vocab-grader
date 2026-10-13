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

st.title("📝 Grit English 채점실행 명령 (취소선 무시 및 수정 답안 인식 모드)")
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

                target_numbers_str = ", ".join(list(answer_dict.keys()))

                # 💡 [핵심 패치]: 가로줄(취소선)이 그어진 단어는 무시하고 새로 고쳐 쓴 최종 단어만 읽도록 지시
                prompt = (
                    "당신은 영단어 및 문장 시험지의 손글씨를 정밀하게 판독하는 수석 채점관입니다.\n"
                    "아래 지정된 문항 번호 목록에 해당하는 학생의 답안을 읽어오세요.\n\n"
                    "📌 [찾아야 할 문항 번호 목록]: {}\n\n"
                    "🚨 [취소선 및 수정 답안 판독 규칙 - 매우 중요]:\n"
                    "1. 학생이 글자 위에 가로로 한 줄 또는 두 줄(취소선)을 그어 지운 단어는 **틀린 오답으로 간주하여 완전히 무시**하십시오.\n"
                    "2. 줄이 그어진 단어 옆이나 위, 혹은 근처에 새로 고쳐 쓴 **최종 단어(수정된 정자)**만을 유효한 학생 답안으로 채택하여 읽으십시오.\n"
                    "3. 오타 자동 보정은 절대 금지하며, 최종적으로 채택된 글자의 실제 알파벳 철자 그대로 읽으십시오.\n"
                    "4. 미응답이거나 답이 없는 경우 '(미응답)'으로 적으십시오.\n\n"
                    "반드시 아래 형식으로만 답변하세요. 다른 설명은 절대 금지합니다.\n"
                    "형식 예시:\n"
                    "1: vacuum\n"
                    "7: bottle\n"
                    "11: pollute\n"
                    "21: soil"
                ).format(target_numbers_str)

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

                    response = client.chat.completions.create(
                        model=selected_model,
                        temperature=0.0,
                        messages=chat_messages
                    )

                    response_text = response.choices[0].message.content
                    
                    gpt_parsed = {}
                    for line in response_text.splitlines():
                        line = line.strip()
                        match = re.search(r"^(\d+)\s*[:\.\-]?\s*(.*)$", line)
                        if match:
                            raw_num = match.group(1)
                            q_val = match.group(2).strip()
                            norm_num = normalize_q_num(raw_num)
                            if norm_num and q_val:
                                gpt_parsed[norm_num] = q_val

                    records = []
                    for q_num_norm, c_ans in answer_dict.items():
                        if q_num_norm in gpt_parsed:
                            raw_written = gpt_parsed[q_num_norm]

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
