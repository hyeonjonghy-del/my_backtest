import streamlit as st
import os

# 페이지 기본 설정
st.set_page_config(
    page_title="Stock Auto Trading Bot",
    page_icon="📈",
    layout="wide"
)

# 메인 화면 디자인
st.title("🤖 주식 자동매매 시스템 (Stock Bot)")
st.markdown("### 새로운 PC에서의 시작을 환영합니다!")
st.markdown("---")

# 안내 메시지
st.info("👈 왼쪽 사이드바(> 화살표)를 열어 실행할 전략을 선택해주세요.")

# 폴더 내 파일 확인 (디버깅용)
st.subheader("📌 시스템 상태 확인")

# pages 폴더가 잘 인식되는지 확인
pages_dir = os.path.join(os.getcwd(), "pages")
if os.path.exists(pages_dir):
    file_count = len([f for f in os.listdir(pages_dir) if f.endswith(".py")])
    st.success(f"✅ 'pages' 폴더가 감지되었습니다. (발견된 전략 파일: {file_count}개)")
    st.markdown("""
    **사용 가능한 기능:**
    - **Momentum Strategy**: 실전 매매 전략
    - **Momentum Backtest**: 과거 수익률 테스트
    """)
else:
    st.error("⚠️ 'pages' 폴더를 찾을 수 없습니다. 현재 폴더 위치를 확인해주세요.")

# 하단 푸터
st.markdown("---")
st.caption("Ver 2.0 | PC: OneDrive Sync Mode | Powered by Python & Streamlit")