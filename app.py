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

start_grading = st.button("🚀 채점 시작", type="primary", use_container_width=True)

if start_grading:
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
                    "#### 📄 [{}/{}] 파일명: `{}` (번호: `{}`)".format(
                        idx + 1, len(student_photos), photo.name, photo_key
                    )
                )

                matched_answer_file = answer_file_map.get(photo_key)
                if not matched_answer_file:
                    st.error(
                        "❌ `{}`에 매칭되는 정답지(`{}`)를 찾지 못했습니다.".format(
                            photo.name, photo_key
                        )
                    )
                    continue

                answer_dict = load_answer_dict_from_file(matched_answer_file)
                base64_image = compress_and_encode_image(photo, max_size=3840)

                prompt = (
                    "당신은 영단어 및 문장 시험지의 학생 손글씨를 한 글자도 빠짐없이 엄격하게 검증하는 초정밀 OCR 판독관입니다.\n\n"
                    "🛑 [자체 보정 및 맥락 추측 절대 금지 (오답 엄격 판독 규칙)]\n"
                    "1. 시험지의 다른 문항에서 동일한 단어가 나왔거나 학생이 맞게 적었더라도, 현재 문항에서 학생이 철자를 다르게 적었거나 알파벳을 빼먹었다면 절대로 자체적으로 올바른 단어로 수정하여 판독하지 마십시오.\n"
                    "2. 오직 해당 문항에 실제로 써진 손글씨 자국만 100% 기준으로 삼아야 합니다. 학생이 실제로 쓴 철자 그대로만 하이픈(-)으로 나누어 추출하세요.\n"
                    "   - 예시: 원래 정답이 'opportunity'일 때, 학생이 1번에서는 제대로 적었으나 5번에서는 'o-p-p-o-r-t-u-n-i-t'로 'y'를 빠뜨리고 적었다면, 5번은 반드시 'o-p-p-o-r-t-u-n-i-t'로 판독하여 오답 처리되도록 해야 합니다.\n\n"
                    "🚨 [단어 끝글자 정밀 스캔 지침]\n"
                    "1. 단, 학생이 끝글자(y, e, g, t, s 등)의 획을 실제로 작게라도 써놓은 경우, 이것을 생략하지 말고 꼼꼼히 판독하여 하이픈으로 적어주어야 합니다.\n"
                    "2. 손글씨 획이 아예 존재하지 않을 때만 해당 알파벳을 누락된 상태로 적으세요.\n"
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
                                    "url": "data:image/jpeg;base64,{}".format(
                                        base64_image
                                    ),
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
                                reason = (
                                    "철자 불일치/누락 (작성: '{}' / 정답: '{}')"
                                    "".format(student_word, correct_word)
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

                st.info(
                    "결과: **{} / {}점** (틀린 문항: {}개)".format(
                        total_correct, total_q_count, wrong_count
                    )
                )

                if wrong_details:
                    df_wrong = pd.DataFrame(wrong_details)
                    st.dataframe(
                        df_wrong, use_container_width=True, hide_index=True
                    )

                    csv_data = df_wrong.to_csv(index=False).encode("utf-8-sig")
                    safe_filename = re.sub(r"[^\w\-_.]", "_", photo.name)
                    st.download_button(
                        label="📥 `{}` 오답노트 다운로드 (CSV)".format(
                            photo.name
                        ),
                        data=csv_data,
                        file_name="오답노트_{}.csv".format(safe_filename),
                        mime="text/csv",
                        key="dl_{}".format(idx),
                    )
                else:
                    st.balloons()
                    st.success("🎉 모든 문항을 맞혔습니다!")

                st.markdown("---")

        except Exception as e:
            st.error("채점 중 오류가 발생했습니다: {}".format(e))
