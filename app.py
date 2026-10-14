import io
import json
import os
import re
import time
import unicodedata
import pandas as pd
import streamlit as st
from datetime import datetime

st.set_page_config(
    page_title="Grit English 다중 유닛 디지털 단어 시험실",
    page_icon="💻",
    layout="wide",
)

st.title("💻 Grit English 학생별 다중 유닛(3장) 맞춤 단어 시험 시스템")
st.write("Made by Jason")

ARCHIVE_FILE = "exam_archive.json"


def load_archive():
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_archive(submissions):
    try:
        with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
            json.dump(submissions, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"저장 오류: {e}")


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFC", str(text)).lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_q_num(q_str: str) -> str:
    if not q_str:
        return ""
    q_clean = str(q_str).strip()
    q_clean = re.sub(r"\.0$", "", q_clean)
    q_clean = re.sub(r"[^\d]", "", q_clean)
    if q_clean.isdigit():
        return str(int(q_clean))
    return str(q_str).strip()


def extract_unit_name(filename: str) -> str:
    name_without_ext = re.sub(r"\.[^.]+$", "", filename)
    return name_without_ext


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


def load_student_roster(roster_file) -> dict:
    bytes_data = roster_file.getvalue()
    f_name = roster_file.name.lower()
    if f_name.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(bytes_data))
    else:
        df = pd.read_excel(io.BytesIO(bytes_data), engine="openpyxl")

    df.columns = [str(c).strip().lower() for c in df.columns]
    
    roster = {}
    for _, row in df.iterrows():
        s_id = str(row.get("id", row.iloc[0])).strip()
        s_pw = str(row.get("password", row.get("pw", row.iloc[1]))).strip()
        s_name = str(row.get("name", row.iloc[2])).strip()
        s_units_raw = str(row.get("unit", row.get("test", row.iloc[3]))).strip()
        s_units = [u.strip() for u in re.split(r"[,/]", s_units_raw) if u.strip()]
        
        roster[s_id] = {
            "password": s_pw,
            "name": s_name,
            "units": s_units
        }
    return roster


# 세션 상태 초기화
if "answer_banks" not in st.session_state:
    st.session_state["answer_banks"] = {}
if "student_roster" not in st.session_state:
    st.session_state["student_roster"] = {}
if "submissions" not in st.session_state:
    st.session_state["submissions"] = load_archive()
if "exam_settings" not in st.session_state:
    st.session_state["exam_settings"] = {"end_time_str": "18:00"}


# ==========================================
# 🛠️ 사이드바: 교사 전용 관리 패널
# ==========================================
st.sidebar.header("🛠️ 교사 전용 관리 패널")
admin_mode = st.sidebar.checkbox("교사 모드 활성화 (관리자용)")

st.sidebar.markdown("---")
st.sidebar.markdown("### 👥 학생 명단(ID/PW/유닛들) 업로드")
st.sidebar.caption("💡 엑셀의 Unit 열에 여러 개를 적을 때 쉼표로 구분하세요 (예: Unit_30, Unit_31, Unit_32)")
roster_file = st.sidebar.file_uploader(
    "학생 명단 엑셀/CSV 파일 업로드",
    type=["xlsx", "xls", "csv"],
    key="admin_roster"
)
if roster_file:
    try:
        st.session_state["student_roster"] = load_student_roster(roster_file)
        st.sidebar.success(f"✅ 총 {len(st.session_state['student_roster'])}명의 학생 계정이 등록되었습니다.")
    except Exception as e:
        st.sidebar.error(f"❌ 파일 형식을 확인해주세요.")

st.sidebar.markdown("---")
st.sidebar.markdown("### 📄 정답지 엑셀 일괄 등록")
admin_answer_files = st.sidebar.file_uploader(
    "정답지 엑셀/CSV 파일 선택 (다중 선택 가능)",
    type=["xlsx", "xls", "csv"],
    accept_multiple_files=True,
    key="admin_ans_multi",
)

if admin_answer_files:
    for ans_f in admin_answer_files:
        unit_name = extract_unit_name(ans_f.name)
        st.session_state["answer_banks"][unit_name] = (
            load_answer_dict_from_file(ans_f)
        )
    st.sidebar.success(
        f"✅ 총 {len(st.session_state['answer_banks'])}개의 시험지 정답 은행이 로드되었습니다."
    )

st.sidebar.markdown("---")
st.sidebar.markdown("### ⏰ 시험 목표 종료 시각 설정")
end_time_input = st.sidebar.text_input(
    "시험 종료 시각 (24시간 형식 HH:MM)", value="18:00", placeholder="예: 17:30"
)
st.session_state["exam_settings"]["end_time_str"] = end_time_input


# ==========================================
# 👨‍🏫 [관리자 모드]: 전체 누적 기록 및 성적 관리
# ==========================================
if admin_mode:
    st.markdown("---")
    st.header("👨‍🏫 [교사 관리 화면] 전체 학생 시험 기록 보관함")

    subs = st.session_state["submissions"]

    if subs:
        summary_list = []
        for key, data in subs.items():
            summary_list.append(
                {
                    "기록ID": key,
                    "학생 이름": data.get("student_name"),
                    "로그인ID": data.get("student_id"),
                    "응시 유닛들": ", ".join(data.get("units", [])),
                    "총점": f"{data.get('total_score')} / {data.get('total_possible')}",
                    "제출 상태": data.get("status"),
                    "응시 시각": data.get("timestamp", "-"),
                }
            )
        df_summary = pd.DataFrame(summary_list)

        col_dl1, col_dl2 = st.columns([2, 2])
        with col_dl1:
            csv_all = df_summary.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="📥 모든 학생 누적 성적 요약 엑셀(CSV) 다운로드",
                data=csv_all,
                file_name="전체_학생_누적성적표.csv",
                mime="text/csv",
                type="primary",
            )
        with col_dl2:
            if st.button("🗑️ 모든 기록 초기화 (주의)", type="secondary"):
                st.session_state["submissions"] = {}
                if os.path.exists(ARCHIVE_FILE):
                    os.remove(ARCHIVE_FILE)
                st.success("모든 기록이 초기화되었습니다.")
                st.rerun()

        st.markdown("---")
        st.write(f"총 누적 응시 기록: **{len(subs)}건**")

        for key, res_data in subs.items():
            with st.expander(
                f"👤 {res_data.get('student_name')} ({res_data.get('student_id')}) | 유닛들: [{', '.join(res_data.get('units', []))}] | 총점: {res_data.get('total_score')} / {res_data.get('total_possible')}점 | {res_data.get('timestamp', '')}"
            ):
                for u_name, u_data in res_data.get("unit_results", {}).items():
                    st.markdown(f"#### 📄 시험지: `{u_name}` (점수: {u_data['score']} / {u_data['total']}점)")
                    df_res = pd.DataFrame(u_data["records"])
                    st.dataframe(df_res, use_container_width=True)
    else:
        st.info("⏳ 아직 보관된 시험 기록이 없습니다.")


# ==========================================
# 📝 [학생 모드]: 위성 랩탑 로그인 및 다중 유닛 응시
# ==========================================
else:
    st.markdown("---")
    st.header("📝 학생 로그인 및 다중 유닛 단어 시험 응시")

    roster = st.session_state["student_roster"]
    answer_banks = st.session_state["answer_banks"]

    if not roster:
        st.warning("⚠️ 아직 선생님이 학생 명단 파일을 등록하지 않았습니다.")
    elif not answer_banks:
        st.warning("⚠️ 아직 선생님이 시험지 정답 파일을 등록하지 않았습니다.")
    else:
        if "logged_in_student" not in st.session_state:
            st.session_state["logged_in_student"] = None

        if not st.session_state["logged_in_student"]:
            st.markdown("### 🔑 학원계정으로 로그인하세요")
            col_l1, col_l2 = st.columns(2)
            with col_l1:
                input_id = st.text_input("아이디", placeholder="예: apple홍길동")
            with col_l2:
                input_pw = st.text_input("비밀번호", type="password", placeholder="비밀번호 입력")

            if st.button("🔓 로그인", type="primary"):
                if input_id in roster and roster[input_id]["password"] == input_pw:
                    st.session_state["logged_in_student"] = input_id
                    st.success(f"환영합니다, {roster[input_id]['name']} 학생!")
                    time.sleep(0.8)
                    st.rerun()
                else:
                    st.error("❌ 아이디 또는 비밀번호가 올바르지 않습니다.")

        else:
            current_id = st.session_state["logged_in_student"]
            student_info = roster[current_id]
            student_name = student_info["name"]
            assigned_units = student_info["units"]

            top_c1, top_c2 = st.columns([4, 1])
            with top_c1:
                st.info(f"👤 접속 계정: **{student_name}** (`{current_id}`) | 배정된 시험지 목록: **{assigned_units}**")
            with top_c2:
                if st.button("🔒 로그아웃"):
                    st.session_state["logged_in_student"] = None
                    st.rerun()

            missing_units = [u for u in assigned_units if u not in answer_banks]
            if missing_units:
                st.error(f"⚠️ 다음 배정된 시험지의 정답 파일이 아직 등록되지 않았습니다: `{missing_units}`. 선생님께 문의하세요!")
            else:
                exam_key_state = f"exam_state_multi_{current_id}"
                if exam_key_state not in st.session_state:
                    st.session_state[exam_key_state] = {
                        "started": False,
                        "submitted": False,
                    }

                state = st.session_state[exam_key_state]

                if not state["started"] and not state["submitted"]:
                    target_end_str = st.session_state["exam_settings"]["end_time_str"]
                    total_q_count = sum([len(answer_banks[u]) for u in assigned_units])
                    st.markdown("---")
                    st.info(
                        f"📌 **[{', '.join(assigned_units)}] 총 {len(assigned_units)}장 시험 준비 완료**\n\n"
                        f"• 전체 문항 수: 총 {total_q_count}문항 (유닛별 각 탭에서 입력)\n"
                        f"• 시험 마감 시각: **오후 {target_end_str}** (랩탑 시계 기준)\n\n"
                        "⚠️ [시험 시작하기]를 누르는 순간 우측 상단에 마감 시각까지의 카운트다운이 시작됩니다."
                    )
                    if st.button("🚀 다중 시험 시작하기", type="primary", use_container_width=True):
                        state["started"] = True
                        st.rerun()

                elif state["started"] and not state["submitted"]:
                    target_end_str = st.session_state["exam_settings"]["end_time_str"]

                    now = datetime.now()
                    try:
                        target_hour, target_min = map(int, target_end_str.split(":"))
                        target_time = now.replace(hour=target_hour, minute=target_min, second=0, microsecond=0)
                        remaining_time = int((target_time - now).total_seconds())
                    except Exception:
                        remaining_time = 300

                    if remaining_time <= 0:
                        state["submitted"] = True
                        
                        unit_results = {}
                        total_score = 0
                        total_possible = 0

                        for u_name in assigned_units:
                            ans_dict = answer_banks[u_name]
                            records = []
                            u_correct = 0
                            for q_num, c_ans in ans_dict.items():
                                raw_ans = st.session_state.get(f"q_{u_name}_{q_num}", "").strip()
                                norm_student = normalize_text(raw_ans)
                                norm_correct = normalize_text(str(c_ans))

                                if not raw_ans:
                                    is_correct, reason = False, "미응답 (시간 종료)"
                                elif norm_student == norm_correct:
                                    is_correct, reason = True, "정답"
                                    u_correct += 1
                                else:
                                    is_correct, reason = False, f"오답 (작성: {raw_ans})"

                                records.append({
                                    "문항 번호": q_num,
                                    "학생 작성 답안": raw_ans if raw_ans else "(공란)",
                                    "교재 정답": str(c_ans),
                                    "정오답": is_correct,
                                    "사유": reason,
                                })
                            unit_results[u_name] = {"score": u_correct, "total": len(ans_dict), "records": records}
                            total_score += u_correct
                            total_possible += len(ans_dict)

                        record_id = f"{current_id}_multi_{int(time.time())}"
                        current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

                        st.session_state["submissions"][record_id] = {
                            "student_id": current_id,
                            "student_name": student_name,
                            "units": assigned_units,
                            "total_score": total_score,
                            "total_possible": total_possible,
                            "status": "마감 시각 도달 자동 제출",
                            "timestamp": current_time_str,
                            "unit_results": unit_results,
                        }
                        save_archive(st.session_state["submissions"])
                        st.rerun()

                    st.markdown("---")
                    head_col1, head_col2 = st.columns([3, 1])
                    with head_col1:
                        st.markdown(f"### ⌨️ 다중 시험 응시 중 ({len(assigned_units)}장 시험지)")
                    with head_col2:
                        rem_mins = remaining_time // 60
                        rem_secs = remaining_time % 60
                        st.markdown(
                            f"""
                            <div style="background-color: #ff4b4b; padding: 8px 12px; border-radius: 8px; text-align: center; color: white; font-weight: bold; font-size: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                                ⏳ {target_end_str}까지<br>{rem_mins:02d}분 {rem_secs:02d}초 남음
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                    st.markdown("")

                    unit_tabs = st.tabs([f"📄 {u}" for u in assigned_units])
                    
                    for idx, u_name in enumerate(assigned_units):
                        with unit_tabs[idx]:
                            ans_dict = answer_banks[u_name]
                            st.write(f"**[{u_name}]** 총 {len(ans_dict)}문항 - 정답을 타이핑하세요.")
                            
                            q_keys = sorted(ans_dict.keys(), key=lambda x: int(x) if x.isdigit() else 0)
                            cols = st.columns(2)
                            for i, q_num in enumerate(q_keys):
                                target_col = cols[i % 2]
                                with target_col:
                                    st.text_input(f"{q_num}번 문항", key=f"q_{u_name}_{q_num}")

                    st.markdown("---")
                    if st.button("🚀 모든 시험지 최종 제출하기", type="primary", use_container_width=True):
                        state["submitted"] = True

                        unit_results = {}
                        total_score = 0
                        total_possible = 0

                        for u_name in assigned_units:
                            ans_dict = answer_banks[u_name]
                            records = []
                            u_correct = 0
                            for q_num, c_ans in ans_dict.items():
                                raw_ans = st.session_state.get(f"q_{u_name}_{q_num}", "").strip()
                                norm_student = normalize_text(raw_ans)
                                norm_correct = normalize_text(str(c_ans))

                                if not raw_ans:
                                    is_correct, reason = False, "미응답"
                                elif norm_student == norm_correct:
                                    is_correct, reason = True, "정답"
                                    u_correct += 1
                                else:
                                    is_correct, reason = False, f"오답 (작성: {raw_ans})"

                                records.append({
                                    "문항 번호": q_num,
                                    "학생 작성 답안": raw_ans if raw_ans else "(공란)",
                                    "교재 정답": str(c_ans),
                                    "정오답": is_correct,
                                    "사유": reason,
                                })
                            unit_results[u_name] = {"score": u_correct, "total": len(ans_dict), "records": records}
                            total_score += u_correct
                            total_possible += len(ans_dict)

                        record_id = f"{current_id}_multi_{int(time.time())}"
                        current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

                        st.session_state["submissions"][record_id] = {
                            "student_id": current_id,
                            "student_name": student_name,
                            "units": assigned_units,
                            "total_score": total_score,
                            "total_possible": total_possible,
                            "status": "정상 제출",
                            "timestamp": current_time_str,
                            "unit_results": unit_results,
                        }
                        save_archive(st.session_state["submissions"])
                        st.rerun()

                    time.sleep(1)
                    st.rerun()

                elif state["submitted"]:
                    user_subs = [
                        v
                        for v in st.session_state["submissions"].values()
                        if v.get("student_id") == current_id
                    ]
                    latest_sub = user_subs[-1] if user_subs else {}

                    st.balloons()
                    st.success(
                        f"🎉 모든 시험이 종료되었습니다! 총점: {latest_sub.get('total_score', 0)} / {latest_sub.get('total_possible', 0)}점"
                    )

                    if latest_sub.get("status") == "마감 시각 도달 자동 제출":
                        st.error("⏰ 시험 종료 시각이 되어 자동 마감 및 제출되었습니다.")

                    for u_name, u_data in latest_sub.get("unit_results", {}).items():
                        st.markdown(f"#### 📄 `{u_name}` 결과: {u_data['score']} / {u_data['total']}점")
                        df_my_res = pd.DataFrame(u_data["records"])
                        df_my_wrong = df_my_res[~df_my_res["정오답"]]
                        if not df_my_wrong.empty:
                            st.markdown("오답 리스트:")
                            st.dataframe(df_my_wrong[["문항 번호", "학생 작성 답안", "교재 정답"]], use_container_width=True)
