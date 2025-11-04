import streamlit as st
import geopandas as gpd
import pandas as pd
import folium
from folium.plugins import MarkerCluster
from folium.features import DivIcon
from shapely.geometry import Point
import osmnx as ox
import requests
from streamlit_folium import st_folium
import openai
import math
import os

# ✅ 환경변수 불러오기 (Streamlit Cloud 호환에 저장된 키 사용)
# ──────────────────────────────
MAPBOX_TOKEN = st.secrets["MAPBOX_TOKEN"]
openai.api_key = st.secrets["OPENAI_API_KEY"]

# ──────────────────────────────
# ✅ 데이터 로드 (안전한 로드)
# ──────────────────────────────
@st.cache_data
def load_data():
    try:
        gdf = gpd.read_file("cb_tour.shp").to_crs(epsg=4326)
        gdf["lon"], gdf["lat"] = gdf.geometry.x, gdf.geometry.y
        boundary = gpd.read_file("cb_shp.shp").to_crs(epsg=4326)
        data = pd.read_csv("cj_data_final.csv", encoding="cp949").drop_duplicates()
        return gdf, boundary, data
    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {str(e)}")
        return None, None, None

gdf, boundary, data = load_data()

# 데이터 로드 실패 시 앱 중단
if gdf is None:
    st.stop()

# csv 파일에 카페 있을때 출력 / 카페 포맷 함수
def format_cafes(cafes_df):
    try:
        cafes_df = cafes_df.drop_duplicates(subset=['c_name', 'c_value', 'c_review'])
        result = []
        
        if len(cafes_df) == 0:
            return ("현재 이 관광지 주변에 등록된 카페 정보는 없어요. \n"
                   "하지만 근처에 숨겨진 보석 같은 공간이 있을 수 있으니, \n"
                   "지도를 활용해 천천히 걸어보시는 것도 추천드립니다 😊")
        elif len(cafes_df) == 1:
            row = cafes_df.iloc[0]
            if all(x not in str(row["c_review"]) for x in ["없음", "없읍"]):
                return f" **{row['c_name']}** (⭐ {row['c_value']}) \n\"{row['c_review']}\""
            else:
                return f"**{row['c_name']}** (⭐ {row['c_value']})"
        else:
            grouped = cafes_df.groupby(['c_name', 'c_value'])
            result.append("**주변의 평점 높은 카페들은 여기 있어요!** 🌼\n")
            
            for (name, value), group in grouped:
                reviews = group['c_review'].dropna().unique()
                reviews = [r for r in reviews if all(x not in str(r) for x in ["없음", "없읍"])]
                top_reviews = reviews[:3]
                
                if top_reviews:
                    review_text = "\n".join([f"\"{r}\"" for r in top_reviews])
                    result.append(f"- **{name}** (⭐ {value}) \n{review_text}")
                else:
                    result.append(f"- **{name}** (⭐ {value})")
            
            return "\n\n".join(result)
            
    except Exception as e:
        return f"카페 정보 처리 중 오류가 발생했습니다: {str(e)}"

# ──────────────────────────────
# ✅ Session 초기화
# ──────────────────────────────
DEFAULTS = {
    "order": [],
    "segments": [],
    "duration": 0.0,
    "distance": 0.0,
    "messages": [{"role": "system", "content": "당신은 청주 문화관광 전문 가이드입니다."}],
    "auto_gpt_input": ""
}

for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ──────────────────────────────
# ✅ 페이지 설정 & 스타일
# ──────────────────────────────
st.set_page_config(
    page_title="청풍로드 - 충청북도 맞춤형 AI기반 스마트 관광 가이드",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
/* 기본 폰트 시스템 */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif;
}

/* 기본 스타일 */
.main > div {
    padding-top: 1.2rem;
    padding-bottom: 0.5rem;
}

header[data-testid="stHeader"] {
    display: none;
}

.stApp {
    background: #f8f9fa;
}

/* 헤더 컨테이너 (로고 + 제목) */
.header-container {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 20px;
    margin-bottom: 2rem;
    padding: 1rem 0;
}

.logo-image {
    width: 50px;
    height: 50px;
    object-fit: contain;
}

.main-title {
    font-size: 2.8rem;
    font-weight: 700;
    color: #202124;
    letter-spacing: -1px;
    margin: 0;
}

.title-underline {
    width: 100%;
    height: 3px;
    background: linear-gradient(90deg, #4285f4, #34a853);
    margin: 0 auto 2rem auto;
    border-radius: 2px;
}

/* 섹션 헤더 스타일 */
.section-header {
    font-size: 1.3rem;
    font-weight: 700;
    color: #1f2937;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 8px;
    padding-bottom: 12px;
    border-bottom: 2px solid #f3f4f6;
}

/* 버튼 스타일 개선 */
.stButton > button {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border: none;
    border-radius: 10px;
    padding: 12px 20px;
    font-size: 0.9rem;
    font-weight: 600;
    width: 100%;
    height: 48px;
    transition: all 0.3s ease;
    box-shadow: 0 4px 8px rgba(102, 126, 234, 0.3);
}

.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 16px rgba(102, 126, 234, 0.4);
}

/* 방문 순서 리스트 스타일 */
.visit-order-item {
    display: flex;
    align-items: center;
    padding: 12px 16px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    border-radius: 12px;
    margin-bottom: 8px;
    font-size: 0.95rem;
    font-weight: 500;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(102, 126, 234, 0.3);
}

.visit-order-item:hover {
    transform: translateX(4px);
    box-shadow: 0 4px 8px rgba(102, 126, 234, 0.4);
}

.visit-number {
    background: rgba(255,255,255,0.9);
    color: #667eea;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.8rem;
    font-weight: 700;
    margin-right: 12px;
    flex-shrink: 0;
}

/* 메트릭 카드 스타일 */
.stMetric {
    background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
    border: none;
    border-radius: 12px;
    padding: 16px 10px;
    text-align: center;
    transition: all 0.2s ease;
    box-shadow: 0 2px 4px rgba(168, 237, 234, 0.3);
}

.stMetric:hover {
    transform: translateY(-2px);
    box-shadow: 0 4px 8px rgba(168, 237, 234, 0.4);
}

/* 빈 상태 메시지 */
.empty-state {
    text-align: center;
    padding: 40px 20px;
    color: #9ca3af;
    font-style: italic;
    font-size: 0.95rem;
    background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
    border-radius: 12px;
    margin: 16px 0;
}

/* 🔧 지도 컨테이너 스타일 - 박스 제거 완전 수정 */
.map-container {
    width: 100% !important;
    height: 520px !important;
    border-radius: 12px !important;
    overflow: hidden !important;
    position: relative !important;
    background: transparent !important;
    border: 2px solid #e5e7eb !important;
    margin: 0 !important;
    padding: 0 !important;
    box-sizing: border-box !important;
}

/* Streamlit iframe 완전 초기화 */
div[data-testid="stIFrame"] {
    width: 100% !important;
    max-width: 100% !important;
    height: 520px !important;
    position: relative !important;
    overflow: hidden !important;
    box-sizing: border-box !important;
    border-radius: 12px !important;
    background: transparent !important;
    border: none !important;
    margin: 0 !important;
    padding: 0 !important;
}

div[data-testid="stIFrame"] > iframe {
    width: 100% !important;
    height: 100% !important;
    border: none !important;
    border-radius: 12px !important;
    max-width: 100% !important;
    box-sizing: border-box !important;
    background: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
}

/* 🚨 핵심: Streamlit 내부 빈 div들 완전 제거 */
div[data-testid="stIFrame"] > iframe > html > body > div:empty {
    display: none !important;
}

div[data-testid="stIFrame"] div:empty {
    display: none !important;
}

/* 🚨 Folium 내부 빈 컨테이너 제거 */
.folium-map div:empty {
    display: none !important;
}

/* 🚨 Leaflet 오버레이 박스 제거 */
.leaflet-container .leaflet-control-container div:empty {
    display: none !important;
}

.leaflet-container > div:empty {
    display: none !important;
}

/* 🚨 모든 빈 오버레이 박스 강제 제거 */
div:empty:not(.leaflet-zoom-box):not(.leaflet-marker-icon):not(.leaflet-div-icon) {
    display: none !important;
}

/* 🚨 투명하거나 흰색 배경의 빈 박스들 제거 */
div[style*="background: white"]:empty,
div[style*="background: #fff"]:empty,
div[style*="background: #ffffff"]:empty,
div[style*="background-color: white"]:empty,
div[style*="background-color: #fff"]:empty,
div[style*="background-color: #ffffff"]:empty {
    display: none !important;
}

/* Folium/Leaflet 지도 자체 크기 제한 */
.folium-map {
    width: 100% !important;
    height: 100% !important;
    max-width: 100% !important;
    max-height: 520px !important;
    box-sizing: border-box !important;
    background: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
}

/* Leaflet 컨테이너 크기 고정 */
.leaflet-container {
    width: 100% !important;
    height: 100% !important;
    max-width: 100% !important;
    max-height: 520px !important;
    box-sizing: border-box !important;
    background: transparent !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
}

/* 폼 스타일 개선 */
.stTextInput > div > div > input,
.stSelectbox > div > div > select {
    border: 2px solid #e5e7eb;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 0.9rem;
    transition: all 0.2s ease;
    background: #fafafa;
}

.stTextInput > div > div > input:focus,
.stSelectbox > div > div > select:focus {
    border-color: #667eea;
    background: white;
    box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 간격 조정 */
.block-container {
    padding-top: 1rem;
    padding-bottom: 1rem;
    max-width: 1400px;
}

/* 성공/경고 메시지 */
.stSuccess {
    background: linear-gradient(135deg, #d4edda 0%, #c3e6cb 100%);
    border: 1px solid #b8dacd;
    border-radius: 8px;
    color: #155724;
}

.stWarning {
    background: linear-gradient(135deg, #fff3cd 0%, #ffeaa7 100%);
    border: 1px solid #f8d7da;
    border-radius: 8px;
    color: #856404;
}

.stError {
    background: linear-gradient(135deg, #f8d7da 0%, #f5c6cb 100%);
    border: 1px solid #f1b0b7;
    border-radius: 8px;
    color: #721c24;
}
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 헤더 (GitHub Raw URL로 로고 이미지 로드)
# ──────────────────────────────
st.markdown('''
<div class="header-container">
    <img src="https://raw.githubusercontent.com/JeongWon4034/cheongju/main/cheongpung_logo.png" alt='청풍로드 로고' style ="width:125px; height:125px">
    <div class="main-title">청풍로드 - 충청북도 맞춤형 AI기반 스마트 관광 가이드</div>
</div>
<div class="title-underline"></div>
''', unsafe_allow_html=True)

# ──────────────────────────────
# ✅ 메인 레이아웃 (3컬럼)
# ──────────────────────────────
col1, col2, col3 = st.columns([1.5, 1.2, 3], gap="large")

# ------------------------------
# ✅ [좌] 경로 설정
# ------------------------------
with col1:
    st.markdown('<div class="section-header">🚗 추천경로 설정</div>', unsafe_allow_html=True)
    
    st.markdown("**이동 모드**")
    mode = st.radio("", ["운전자", "도보"], horizontal=True, key="mode_key", label_visibility="collapsed")
    
    st.markdown("**출발지**")
    start = st.selectbox("", gdf["name"].dropna().unique(), key="start_key", label_visibility="collapsed")
    
    st.markdown("**경유지**")
    wps = st.multiselect("", [n for n in gdf["name"].dropna().unique() if n != st.session_state.get("start_key", "")], key="wps_key", label_visibility="collapsed")
    
    col_btn1, col_btn2 = st.columns(2, gap="small")
    with col_btn1:
        create_clicked = st.button("경로 생성")
    with col_btn2:
        clear_clicked = st.button("초기화")

# ------------------------------
# ✅ 초기화 처리 개선
# ------------------------------
if clear_clicked:
    try:
        keys_to_clear = ["segments", "order", "duration", "distance", "auto_gpt_input"]
        for k in keys_to_clear:
            if k in st.session_state:
                if k in ["segments", "order"]:
                    st.session_state[k] = []
                elif k in ["duration", "distance"]:
                    st.session_state[k] = 0.0
                else:
                    st.session_state[k] = ""
        
        widget_keys = ["mode_key", "start_key", "wps_key"]
        for widget_key in widget_keys:
            if widget_key in st.session_state:
                del st.session_state[widget_key]
        
        st.success("✅ 초기화가 완료되었습니다.")
        st.rerun()
    except Exception as e:
        st.error(f"❌ 초기화 중 오류: {str(e)}")

# ------------------------------
# ✅ [중간] 방문순서 + 메트릭
# ------------------------------
with col2:
    st.markdown('<div class="section-header">📍 여행 방문 순서</div>', unsafe_allow_html=True)
    
    current_order = st.session_state.get("order", [])
    if current_order:
        for i, name in enumerate(current_order, 1):
            st.markdown(f'''
            <div class="visit-order-item">
                <div class="visit-number">{i}</div>
                <div>{name}</div>
            </div>
            ''', unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-state">경로 생성 후 표시됩니다<br>🗺️</div>', unsafe_allow_html=True)
    
    st.markdown("---")
    st.metric("⏱️ 소요시간", f"{st.session_state.get('duration', 0.0):.1f}분")
    st.metric("📏 이동거리", f"{st.session_state.get('distance', 0.0):.2f}km")

# ------------------------------
# ✅ [우] 지도
# ------------------------------
with col3:
    st.markdown('<div class="section-header">🗺️ 추천경로 지도시각화</div>', unsafe_allow_html=True)
    
    # 지도 설정
    try:
        ctr = boundary.geometry.centroid
        clat, clon = float(ctr.y.mean()), float(ctr.x.mean())
        if math.isnan(clat) or math.isnan(clon):
            clat, clon = 36.64, 127.48
    except Exception as e:
        st.warning(f"중심점 계산 오류: {str(e)}")
        clat, clon = 36.64, 127.48

    @st.cache_data
    def load_graph(lat, lon):
        try:
            return ox.graph_from_point((lat, lon), dist=3000, network_type="all")
        except Exception as e:
            st.warning(f"도로 네트워크 로드 실패: {str(e)}")
            try:
                return ox.graph_from_point((36.64, 127.48), dist=3000, network_type="all")
            except:
                return None

    G = load_graph(clat, clon)
    edges = None
    
    if G is not None:
        try:
            edges = ox.graph_to_gdfs(G, nodes=False)
        except Exception as e:
            st.warning(f"엣지 변환 실패: {str(e)}")

    stops = [start] + wps
    snapped = []

    # 개선된 스냅핑
    try:
        for nm in stops:
            matching_rows = gdf[gdf["name"] == nm]
            if matching_rows.empty:
                st.warning(f"⚠️ '{nm}' 정보를 찾을 수 없습니다.")
                continue
            
            r = matching_rows.iloc[0]
            
            if pd.isna(r.lon) or pd.isna(r.lat):
                st.warning(f"⚠️ '{nm}'의 좌표 정보가 없습니다.")
                continue
            
            pt = Point(r.lon, r.lat)
            
            if edges is None or edges.empty:
                snapped.append((r.lon, r.lat))
                continue
            
            edges["d"] = edges.geometry.distance(pt)
            if edges["d"].empty:
                snapped.append((r.lon, r.lat))
                continue
            
            ln = edges.loc[edges["d"].idxmin()]
            sp = ln.geometry.interpolate(ln.geometry.project(pt))
            snapped.append((sp.x, sp.y))
            
    except Exception as e:
        st.error(f"❌ 지점 처리 중 오류: {str(e)}")
        snapped = []
        for nm in stops:
            try:
                r = gdf[gdf["name"] == nm].iloc[0]
                if not (pd.isna(r.lon) or pd.isna(r.lat)):
                    snapped.append((r.lon, r.lat))
            except Exception as coord_error:
                st.warning(f"⚠️ '{nm}' 좌표를 가져올 수 없습니다: {str(coord_error)}")

    # 경로 생성 처리
    if create_clicked and len(snapped) >= 2:
        try:
            segs, td, tl = [], 0.0, 0.0
            api_mode = "walking" if mode == "도보" else "driving"
            
            for i in range(len(snapped) - 1):
                x1, y1 = snapped[i]
                x2, y2 = snapped[i + 1]
                coord = f"{x1},{y1};{x2},{y2}"
                
                url = f"https://api.mapbox.com/directions/v5/mapbox/{api_mode}/{coord}"
                params = {
                    "geometries": "geojson",
                    "overview": "full",
                    "access_token": MAPBOX_TOKEN
                }
                
                try:
                    r = requests.get(url, params=params, timeout=10)
                    if r.status_code == 200:
                        data_resp = r.json()
                        if data_resp.get("routes") and len(data_resp["routes"]) > 0:
                            route = data_resp["routes"][0]
                            segs.append(route["geometry"]["coordinates"])
                            td += route.get("duration", 0)
                            tl += route.get("distance", 0)
                        else:
                            st.warning(f"⚠️ 구간 {i+1}의 경로를 찾을 수 없습니다.")
                    else:
                        st.warning(f"⚠️ API 호출 실패 (상태코드: {r.status_code})")
                except requests.exceptions.Timeout:
                    st.warning("⚠️ API 호출 시간 초과")
                except Exception as api_error:
                    st.warning(f"⚠️ API 호출 오류: {str(api_error)}")
            
            if segs:
                st.session_state["order"] = stops
                st.session_state["duration"] = td / 60
                st.session_state["distance"] = tl / 1000
                st.session_state["segments"] = segs
                st.success("✅ 경로가 성공적으로 생성되었습니다!")
                st.rerun()
            else:
                st.error("❌ 모든 구간의 경로 생성에 실패했습니다.")
                
        except Exception as e:
            st.error(f"❌ 경로 생성 중 오류 발생: {str(e)}")
            st.info("💡 다른 출발지나 경유지를 선택해보세요.")

    # 🔧 지도 렌더링 - 빈 박스 제거 최적화
    try:
        m = folium.Map(
            location=[clat, clon], 
            zoom_start=12, 
            tiles="CartoDB Positron",
            # 🚨 추가 옵션으로 오버레이 방지
            prefer_canvas=True,
            control_scale=True
        )
        
        if boundary is not None:
            folium.GeoJson(boundary, style_function=lambda f: {
                "color": "#9aa0a6",
                "weight": 2,
                "dashArray": "4,4",
                "fillOpacity": 0.05
            }).add_to(m)
        
        mc = MarkerCluster().add_to(m)
        for _, row in gdf.iterrows():
            if not (pd.isna(row.lat) or pd.isna(row.lon)):
                folium.Marker([row.lat, row.lon], 
                            popup=folium.Popup(str(row["name"]), max_width=200),
                            tooltip=str(row["name"]),
                            icon=folium.Icon(color="gray")).add_to(mc)
        
        current_order = st.session_state.get("order", stops)
        for idx, (x, y) in enumerate(snapped, 1):
            if idx <= len(current_order):
                place_name = current_order[idx - 1]
            else:
                place_name = f"지점 {idx}"
            
            folium.Marker([y, x], 
                        icon=folium.Icon(color="red", icon="flag"),
                        tooltip=f"{idx}. {place_name}",
                        popup=folium.Popup(f"<b>{idx}. {place_name}</b>", max_width=200)
            ).add_to(m)
        
        if st.session_state.get("segments"):
            palette = ["#4285f4", "#34a853", "#ea4335", "#fbbc04", "#9c27b0", "#ff9800"]
            segments = st.session_state["segments"]
            
            used_positions = []
            min_distance = 0.001
            
            for i, seg in enumerate(segments):
                if seg:
                    folium.PolyLine([(pt[1], pt[0]) for pt in seg], 
                                  color=palette[i % len(palette)], 
                                  weight=5, 
                                  opacity=0.8
                    ).add_to(m)
                    
                    mid = seg[len(seg) // 2]
                    candidate_pos = [mid[1], mid[0]]
                    
                    while any(abs(candidate_pos[0] - used[0]) < min_distance and 
                            abs(candidate_pos[1] - used[1]) < min_distance 
                            for used in used_positions):
                        candidate_pos[0] += min_distance * 0.5
                        candidate_pos[1] += min_distance * 0.5
                    
                    folium.map.Marker(candidate_pos,
                        icon=DivIcon(html=f"<div style='background:{palette[i % len(palette)]};"
                                        "color:#fff;border-radius:50%;width:28px;height:28px;"
                                        "line-height:28px;text-align:center;font-weight:600;"
                                        "box-shadow:0 2px 4px rgba(0,0,0,0.3);'>"
                                        f"{i+1}</div>")
                    ).add_to(m)
                    
                    used_positions.append(candidate_pos)
            
            try:
                pts = [pt for seg in segments for pt in seg if seg]
                if pts:
                    m.fit_bounds([[min(p[1] for p in pts), min(p[0] for p in pts)],
                                [max(p[1] for p in pts), max(p[0] for p in pts)]])
            except:
                m.location = [clat, clon]
                m.zoom_start = 12
        else:
            m.location = [clat, clon]
            m.zoom_start = 12
        
        # 🚨 레이어 컨트롤 제거 - 빈 박스 원인 가능성
        # folium.LayerControl().add_to(m)
        
        # 🔧 지도 컨테이너 - 완전 수정된 구조
        st.markdown('<div class="map-container">', unsafe_allow_html=True)
        map_data = st_folium(
            m,
            width="100%",
            height=520,
            returned_objects=[],  # 🚨 빈 객체 반환 방지
            use_container_width=True,
            key="main_map"
        )
        st.markdown('</div>', unsafe_allow_html=True)
        
    except Exception as map_error:
        st.error(f"❌ 지도 렌더링 오류: {str(map_error)}")
        st.markdown('<div class="map-container" style="display: flex; align-items: center; justify-content: center; color: #6b7280;">지도를 불러올 수 없습니다.</div>', unsafe_allow_html=True)

# OpenAI 클라이언트 초기화
client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# ------------------------------
# ✅ GPT 가이드
# ------------------------------
st.markdown("---")
st.markdown('<div class="section-header">🤖 생성형 AI기반 관광 가이드</div>', unsafe_allow_html=True)

if st.button("🔁 방문 순서 자동 입력"):
    st.session_state["auto_gpt_input"] = ", ".join(st.session_state.get("order", []))

if "messages" not in st.session_state:
    st.session_state["messages"] = []

with st.form("chat_form"):
    user_input = st.text_input("관광지명을 쉼표로 구분해서 입력하세요", 
                             value=st.session_state.get("auto_gpt_input", ""))
    submitted = st.form_submit_button("🔍 관광지 정보 요청")

if submitted and user_input and client is not None:
    if st.session_state["order"]:
        st.markdown("---")
        st.markdown("## ✨ 관광지별 상세 정보")
        
        for place in st.session_state["order"][:3]:
            try:
                matched = data[data['t_name'].str.contains(place, na=False)]
            except Exception as e:
                st.warning(f"데이터 검색 중 오류: {str(e)}")
                matched = pd.DataFrame()
            
            # GPT 간략 소개
            gpt_intro = ""
            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "당신은 청주 지역의 문화 관광지를 간단하게 소개하는 관광 가이드입니다. "},
                        {"role": "system", "content": "존댓말을 사용하세요."},
                        {"role": "user", "content": f"{place}를 두 문단 이내로 간단히 설명해주세요."}
                    ]
                )
                gpt_intro = response.choices[0].message.content
            except Exception as e:
                gpt_intro = f"❌ GPT 호출 실패: {place} 소개를 불러올 수 없어요. (오류: {str(e)})"
            
            score_text = ""
            review_block = ""
            cafe_info = ""
            
            if not matched.empty:
                try:
                    # 평점
                    t_value = matched['t_value'].dropna().unique()
                    score_text = f"📊**관광지 평점**: ⭐ {t_value[0]}" if len(t_value) > 0 else ""
                    
                    # 리뷰
                    reviews = matched['t_review'].dropna().unique()
                    reviews = [r for r in reviews if all(x not in str(r) for x in ["없음", "없읍"])]
                    if reviews:
                        review_text = "\n".join([f'"{r}"' for r in reviews[:3]])
                        review_block = review_text
                    
                    # 카페
                    cafes = matched[['c_name', 'c_value', 'c_review']].drop_duplicates()
                    cafe_info = format_cafes(cafes)
                    
                except Exception as e:
                    st.warning(f"데이터 처리 중 오류: {str(e)}")
                    cafe_info = "데이터 처리 중 오류가 발생했습니다."
            else:
                cafe_info = (
                    "현재 이 관광지 주변에 등록된 카페 정보는 없어요. \n"
                    "하지만 근처에 숨겨진 보석 같은 공간이 있을 수 있으니, \n"
                    "지도를 활용해 천천히 걸어보시는 것도 추천드립니다 😊"
                )
            
            # 내용 출력
            st.markdown(f"### 🏛️ {place}")
            if score_text:
                st.markdown(score_text)
            
            st.markdown("#### ✨ 소개")
            st.markdown(gpt_intro.strip())
            
            if cafe_info:
                st.markdown("#### 🧋 주변 카페 추천")
                st.markdown(cafe_info.strip())
            
            if review_block:
                st.markdown("#### 💬 방문자 리뷰")
                for review in review_block.split("\n"):
                    if review.strip():
                        st.markdown(f"- {review.strip('\"')}")

elif submitted and user_input and client is None:
    st.error("❌ OpenAI 클라이언트가 초기화되지 않았습니다.")
