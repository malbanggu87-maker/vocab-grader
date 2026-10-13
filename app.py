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
    page_title="Grit English 스마트 채점실", page_icon="📝", layout="wide"
)

st.title("📝 Grit English 스마트 채점실 (정답 매칭 안전장치 강화 버전)")
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
)


def extract_key_number(filename: str) -> str:
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    numbers = re.findall(r"\d+", name_without_ext)
    if numbers:
        last_num = numbers[-1]
        return last_num.zfill(2)[-2:]
    return ""


def normalize_q_num(q_str: str) -> str:
    if not q_str:
        return ""
    q_clean = str(q_str).strip()
    q_clean = re.sub(r"\.0$", "", q_clean)
    q_clean = re.sub(r"[^\d]", "", q_clean)
    if q_clean.isdigit():
        return str(int(q_clean))
    return str(q_str).strip()


def prepare_image(uploaded_file):
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
    width, height = image.size
    top = int(height * 0.15)
    bottom = int(height * 0.96)
    mid_x = int(width * 0.50)

    left_col = image.crop((0, top, mid_x, bottom))
    right_col = image.crop((mid_x, top, width, bottom))
    return left_col, right_col


def crop_question_box_dynamic(image: Image.Image, q_num: int, total_q_count: int = 36):
    width, height = image.size
    if total_q_count <= 25:
        top_margin = height * 0.155
        mid_table_y = height * 0.525
        if 1 <= q_num <= 10:
            row_h = (mid_table_y - top_margin) / 10.0
            idx = q_num - 1
            x1, x2 = int(width * 0.25), int(width * 0.51)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))
        elif 11 <= q_num <= 20:
            row_h = (mid_table_y - top_margin) / 10.0
            idx = q_num - 11
            x1, x2 = int(width * 0.72), int(width * 0.99)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))
        else:
            bot_top = height * 0.54
            bot_bot = height * 0.95
            sub_h = (bot_bot - bot_top) / 5.0
            idx = q_num - 21
            x1, x2 = int(width * 0.20), int(width * 0.99)
            y1 = int(bot_top + (idx * sub_h))
            y2 = int(bot_top + ((idx + 1) * sub_h))
    else:
        top_margin = height * 0.17
        bottom_margin = height * 0.96
        content_height = bottom_margin - top_margin
        row_h = content_height / 18.0
        if 1 <= q_num <= 18:
            idx = q_num - 1
            x1, x2 = int(width * 0.25), int(width * 0.53)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))
        else:
            idx = q_num - 19
            x1, x2 = int(width * 0.71), int(width * 1.0)
            y1 = int(top_margin + (idx * row_h))
            y2 = int(top_margin + ((idx + 1) * row_h))

    padding_x = int((x2 - x1) * 0.06)
    padding_y = int((y2 - y1) * 0.25)
    
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

answer_file_map = {}
if answer_files:
    for ans_f in answer_files:
        a_key = extract_key_number(ans_f.name)
        if a_key:
            answer_file_map[a_key] = ans_f

st.markdown("---")

start_grading = st.button("🚀 정밀 채점 시작", type="primary", use_container_width=True)

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
            for idx, photo in enumerate(student_photos):
                photo_key = extract_key_number(photo.name)
                matched_answer_file = answer_file_map.get(photo_key)
                
                # 🛡️ [안전장치]: 매칭되는 정답지가 없으면 강단 차단 및 경고 출력
                if not matched_answer_file:
                    st.error("❌ 파일명 `{}`에 해당하는 번호(`{}`)의 정답지 파일을 찾지 못했습니다! 정답지 파일명에 해당 유닛 번호(예: `30`)가 포함되어 있는지 확인해주세요.".format(photo.name, photo_key))
                    continue

                answer_dict = load_answer_dict_from_file(matched_answer_file)
                if not answer_dict:
                    st.error("❌ 정답지 파일 `{}`의 내용을 읽어오지 못했거나 데이터가 비어 있습니다.".format(matched_answer_file.name))
                    continue

                image = prepare_image(photo)
                left_col_img, right_col_img = crop_columns(image)

                left_b64 = image_to_base64(left_col_img)
                right_b64 = image_to_base64(right_col_img)

                target_numbers = sorted([int(k) for k in answer_dict.keys() if k.isdigit()])
                target_numbers_str = ", ".join([str(n) for n in target_numbers])

                system_role = (
                    "당신은 정밀 문자 판독(OCR) 및 채점 검증 시스템입니다.\n"
                    "제공된 이미지에서 지정된 문항 번호들에 해당하는 학생의 손글씨 답안을 읽고 교재 정답과 비교하십시오.\n"
                    "판정 상태를 다음 3가지 중 하나로 분류하여 각 줄마다 출력하십시오:\n"
                    "1. [확실한정답]: 학생 글씨가 정답과 완벽히 일치함\n"
                    "2. [자체보정정답]: 학생 글씨에 오타/흘림이 있으나 문맥상 정답으로 유추·보정함\n"
                    "3. [오답]: 명백히 틀렸거나 미응답임\n\n"
                    "출력 형식: 번호: [상태] | 읽은답안"
                )

                prompt = (
                    "첫 번째 이미지(좌측 칼럼)와 두 번째 이미지(우측 칼럼)를 보고 아래 문항들을 채점하세요.\n\n"
                    "📌 [채점할 문항 목록과 정답]:\n"
                )
                for q_n in target_numbers_str.split(", "):
                    ans_val = answer_dict.get(q_n, "")
                    prompt += f"- {q_n}번: 정답({ans_val})\n"

                try:
                    chat_messages = [
                        {"role": "system", "content": system_role},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{}".format(left_b64), "detail": "high"}},
                                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,{}".format(right_b64), "detail": "high"}}
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
                    flagged_items = set()

                    for line in response_text.splitlines():
                        line = line.strip()
                        match = re.search(r"^(\d+)\s*[:\.\-]?\s*\[?(확실한정답\vert{}자체보정정답\vert{}오답)\]?\s*\|?\s*(.*)$", line)
                        if match:
                            raw_num = match.group(1)
                            status = match.group(2)
                            q_val = match.group(3).strip()
                            norm_num = normalize_q_num(raw_num)
                            if norm_num:
                                gpt_parsed[norm_num] = q_val
                                if status in ["자체보정정답", "오답"]:
                                    flagged_items.add(norm_num)

                    records = []
                    total_q_count = len(answer_dict)
                    for q_num_norm, c_ans in answer_dict.items():
                        raw_written = gpt_parsed.get(q_num_norm, "(미응답)")
                        
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
                                reason = "확실한 정답"
                            else:
                                if q_num_norm in flagged_items and "[자체보정정답]" in response_text:
                                    is_correct = True
                                    reason = "AI 자체 보정 정답 (확인 필요)"
                                else:
                                    is_correct = False
                                    reason = "철자 오류 (작성: '{}' / 정답: '{}')".format(raw_written, str(c_ans).strip())

                        is_flagged = (q_num_norm in flagged_items) or (not is_correct)

                        records.append({
                            "문항 번호": q_num_norm,
                            "학생 작성 답안": raw_written if raw_written != "(미응답)" else "(미응답)",
                            "교재 정답": str(c_ans).strip(),
                            "정오답": is_correct,
                            "사유": reason,
                            "검수필요": is_flagged
                        })

                    st.session_state["records_{}".format(idx)] = records
                    st.session_state["photo_{}".format(idx)] = image
                    st.session_state["total_q_{}".format(idx)] = total_q_count

                except Exception as err:
                    st.error("채점 오류 발생: {}".format(err))

for idx, photo in enumerate(student_photos if student_photos else []):
    rec_key = "records_{}".format(idx)
    if rec_key in st.session_state:
        records = st.session_state[rec_key]
        full_img = st.session_state["photo_{}".format(idx)]
        total_q = st.session_state["total_q_{}".format(idx)]

        st.markdown("---")
        st.markdown(f"### 📄 파일명: `{photo.name}` 채점 완료")

        correct_count = sum(1 for r in records if r["정오답"])
        review_items = [r for r in records if r["검수필요"]]

        st.info(f"📊 최종 점수: **{correct_count} / {len(records)}점** | 🤖 AI 완벽 자동 통과: **{len(records) - len(review_items)}개** | ⚠️ 확인이 필요한 검수 문항(자체보정/오답): **{len(review_items)}개**")

        if review_items:
            st.markdown("#### 🔍 [스마트 예외 검수] AI가 헷갈려 '자체 보정'했거나 '오답' 처리한 항목만 확인하세요")
            for item in review_items:
                q_num = item["문항 번호"]
                is_currently_correct = item["정오답"]

                col1, col2, col3 = st.columns([1, 2, 1])

                with col1:
                    st.write(f"**{q_num}번 문항**")
                    st.write(f"학생 작성: `{item['학생 작성 답안']}`")
                    st.write(f"교재 정답: **{item['교재 정답']}**")
                    if is_currently_correct:
                        st.warning(f"상태: 정답 ({item['사유']})")
                    else:
                        st.error(f"상태: 오답 ({item['사유']})")

                with col2:
                    if q_num.isdigit():
                        cropped = crop_question_box_dynamic(full_img, int(q_num), total_q)
                        st.image(cropped, caption="학생 실제 손글씨 (확대)", width=280)

                with col3:
                    st.write("")
                    st.write("")
                    if is_currently_correct:
                        if st.button("❌ 틀렸음 (오답으로 내리기)", key=f"to_wrong_{idx}_{q_num}"):
                            for r in records:
                                if r["문항 번호"] == q_num:
                                    r["정오답"] = False
                                    r["검수필요"] = False
                                    r["사유"] = "선생님 검수: 오답 정정"
                            st.session_state[rec_key] = records
                            st.rerun()
                    else:
                        if st.button("⭕ 실제론 정답임", key=f"to_corr_{idx}_{q_num}"):
                            for r in records:
                                if r["문항 번호"] == q_num:
                                    r["정오답"] = True
                                    r["검수필요"] = False
                                    r["사유"] = "선생님 검수: 정답 인정"
                            st.session_state[rec_key] = records
                            st.rerun()
        else:
            st.success("🎉 AI가 모든 문항을 완벽하게 처리하여 검수할 항목이 없습니다!")

        st.markdown("---")

        # 최종 오답노트 다운로드 버튼
        df_wrong = pd.DataFrame([r for r in records if not r["정오답"]])
        if not df_wrong.empty:
            csv_data = df_wrong[["문항 번호", "학생 작성 답안", "교재 정답", "사유"]].to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 최종 확정 오답노트 다운로드 (CSV)",
                data=csv_data,
                file_name="오답노트_{}.csv".format(photo.name),
                key="dl_final_{}".format(idx)
            )
        else:
            st.balloons()
            st.success("🎉 최종 확정된 오답이 없습니다!")
