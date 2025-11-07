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
from urllib.parse import quote
import io
from itertools import permutations

# ✅ 페이지 설정
st.set_page_config(
    page_title="제주온 - 제주도 맞춤형 AI기반 스마트 관광 가이드",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ✅ 환경변수
MAPBOX_TOKEN = st.secrets["MAPBOX_TOKEN"]
openai.api_key = st.secrets["OPENAI_API_KEY"]

# ✅ 데이터 로드
@st.cache_data
def load_data():
    try:
        tour = pd.read_csv("dataset/관광업_좌표추가.csv", encoding="utf-8").rename(columns={"X": "lon", "Y": "lat"})
        tour["type"] = "관광업"

        cafe = pd.read_csv("dataset/음식점_카페_좌표추가.csv", encoding="utf-8").rename(columns={"X": "lon", "Y": "lat"})
        cafe["type"] = "음식점/카페"

        natural = pd.read_csv("dataset/자연경관_좌표추가.csv", encoding="cp949").rename(columns={"X": "lon", "Y": "lat"})
        natural["type"] = "자연경관"

        if len(tour) > 100:
            tour = tour.sample(n=100, random_state=42)
        if len(cafe) > 100:
            cafe = cafe.sample(n=100, random_state=42)

        data = pd.concat([tour, cafe, natural], ignore_index=True)
        data = data.drop_duplicates(subset=["사업장명", "lon", "lat"])

        geometry = [Point(xy) for xy in zip(data["lon"], data["lat"])]
        gdf = gpd.GeoDataFrame(data, geometry=geometry, crs="EPSG:4326")

        boundary = ox.geocode_to_gdf("Jeju Island, South Korea")
        return gdf, boundary, data
    except Exception as e:
        st.error(f"❌ 데이터 로드 실패: {str(e)}")
        return None, None, None

@st.cache_data
def load_restaurant_data():
    try:
        df = pd.read_csv("final_result.csv", encoding="cp949")
        return df
    except:
        try:
            df = pd.read_csv("final_result.csv", encoding="utf-8")
            return df
        except Exception as e:
            st.warning(f"⚠️ 맛집 데이터 로드 실패: {str(e)}")
            return None

gdf, boundary, data = load_data()
restaurant_df = load_restaurant_data()
data_loaded = gdf is not None

if not data_loaded:
    st.warning("⚠️ 관광 데이터 로드에 실패했어요.")

# ✅ 카페 포맷 함수
def format_cafes(cafes_df):
    try:
        cafes_df = cafes_df.drop_duplicates(subset=['c_name', 'c_value', 'c_review'])
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
            lines = ["**주변의 평점 높은 카페들은 여기 있어요!** 🌼\n"]
            for (name, value), group in grouped:
                reviews = group['c_review'].dropna().unique()
                reviews = [r for r in reviews if all(x not in str(r) for x in ["없음", "없읍"])]
                top_reviews = reviews[:3]
                if top_reviews:
                    review_text = "\n".join([f"\"{r}\"" for r in top_reviews])
                    lines.append(f"- **{name}** (⭐ {value}) \n{review_text}")
                else:
                    lines.append(f"- **{name}** (⭐ {value})")
            return "\n\n".join(lines)
    except Exception as e:
        return f"카페 정보 처리 중 오류가 발생했습니다: {str(e)}"

# ✅ 좌표 가져오기 함수
def get_coordinates(place_name):
    """장소명으로 좌표 가져오기"""
    # 기존 데이터에서 찾기
    matching_rows = gdf[gdf["사업장명"] == place_name]
    if not matching_rows.empty:
        r = matching_rows.iloc[0]
        return (r.lon, r.lat)
    
    # restaurant_df에서 찾기
    if restaurant_df is not None:
        tourist_rows = restaurant_df[restaurant_df["name_2"] == place_name]
        if not tourist_rows.empty:
            r = tourist_rows.iloc[0]
            return (r["X_2"], r["Y_2"])
    
    return None

# ✅ 최단거리 경로 계산 함수
def calculate_shortest_route(start, waypoints, mode="driving"):
    """최단거리 기준으로 경로 최적화"""
    if not waypoints:
        return [start], [], 0.0, 0.0
    
    # 모든 지점의 좌표 가져오기
    all_places = [start] + waypoints
    coords_dict = {}
    for place in all_places:
        coord = get_coordinates(place)
        if coord and not (pd.isna(coord[0]) or pd.isna(coord[1])):
            coords_dict[place] = coord
    
    if len(coords_dict) < 2:
        return [start], [], 0.0, 0.0
    
    # 출발지 좌표
    start_coord = coords_dict.get(start)
    if not start_coord:
        return [start], [], 0.0, 0.0
    
    # 경유지만 추출
    valid_waypoints = [w for w in waypoints if w in coords_dict]
    
    if not valid_waypoints:
        return [start], [], 0.0, 0.0
    
    # 경유지가 5개 이하면 모든 순열 시도, 많으면 그리디 알고리즘
    if len(valid_waypoints) <= 5:
        best_order = None
        best_distance = float('inf')
        
        for perm in permutations(valid_waypoints):
            total_dist = 0
            current = start_coord
            
            for place in perm:
                next_coord = coords_dict[place]
                dist = math.sqrt((current[0] - next_coord[0])**2 + (current[1] - next_coord[1])**2)
                total_dist += dist
                current = next_coord
            
            if total_dist < best_distance:
                best_distance = total_dist
                best_order = list(perm)
        
        optimized_waypoints = best_order
    else:
        # 그리디 알고리즘: 가장 가까운 지점부터 방문
        remaining = valid_waypoints.copy()
        optimized_waypoints = []
        current = start_coord
        
        while remaining:
            nearest = min(remaining, key=lambda p: math.sqrt(
                (current[0] - coords_dict[p][0])**2 + 
                (current[1] - coords_dict[p][1])**2
            ))
            optimized_waypoints.append(nearest)
            current = coords_dict[nearest]
            remaining.remove(nearest)
    
    # 최적화된 순서로 경로 계산
    final_order = [start] + optimized_waypoints
    segments = []
    total_duration = 0.0
    total_distance = 0.0
    
    api_mode = "walking" if mode == "도보" else "driving"
    
    for i in range(len(final_order) - 1):
        coord1 = coords_dict[final_order[i]]
        coord2 = coords_dict[final_order[i + 1]]
        
        coord_str = f"{coord1[0]},{coord1[1]};{coord2[0]},{coord2[1]}"
        url = f"https://api.mapbox.com/directions/v5/mapbox/{api_mode}/{coord_str}"
        params = {"geometries": "geojson", "overview": "full", "access_token": MAPBOX_TOKEN}
        
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data_resp = r.json()
                if data_resp.get("routes"):
                    route = data_resp["routes"][0]
                    segments.append(route["geometry"]["coordinates"])
                    total_duration += route.get("duration", 0)
                    total_distance += route.get("distance", 0)
                else:
                    # API 실패시 직선 거리로 대체
                    segments.append([[coord1[0], coord1[1]], [coord2[0], coord2[1]]])
            else:
                segments.append([[coord1[0], coord1[1]], [coord2[0], coord2[1]]])
        except Exception as e:
            st.warning(f"경로 계산 중 오류: {str(e)}")
            segments.append([[coord1[0], coord1[1]], [coord2[0], coord2[1]]])
    
    return final_order, segments, total_duration / 60, total_distance / 1000

# ✅ Session 초기화
DEFAULTS = {
    "order": [],
    "segments": [],
    "duration": 0.0,
    "distance": 0.0,
    "messages": [{"role": "system", "content": "당신은 제주 문화관광 전문 가이드입니다."}],
    "auto_gpt_input": "",
    "selected_restaurants": []
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ✅ 스타일 (CSS)
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans KR', -apple-system, BlinkMacSystemFont, sans-serif; }
.main > div { padding-top: 1.2rem; padding-bottom: 0.5rem; }
header[data-testid="stHeader"] { display: none; }
.stApp { background: #f8f9fa; }
.header-container { display:flex; align-items:center; justify-content:center; gap:20px; margin-bottom:2rem; padding:1rem 0; }
.logo-image { width:50px; height:50px; object-fit:contain; }
.main-title { font-size:2.8rem; font-weight:700; color:#202124; letter-spacing:-1px; margin:0; }
.title-underline { width:100%; height:3px; background:linear-gradient(90deg,#4285f4,#34a853); margin:0 auto 2rem auto; border-radius:2px; }
.section-header { font-size:1.3rem; font-weight:700; color:#1f2937; margin-bottom:20px; display:flex; align-items:center; gap:8px; padding-bottom:12px; border-bottom:2px solid #f3f4f6; }
.stButton > button { background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:#fff; border:none; border-radius:10px; padding:12px 20px; font-size:0.9rem; font-weight:600; width:100%; height:48px; transition:all .3s; box-shadow:0 4px 8px rgba(102,126,234,.3); }
.stButton > button:hover { transform:translateY(-2px); box-shadow:0 6px 16px rgba(102,126,234,.4); }
.visit-order-item { display:flex; align-items:center; padding:12px 16px; background:linear-gradient(135deg,#667eea 0%,#764ba2 100%); color:#fff; border-radius:12px; margin-bottom:8px; font-size:.95rem; font-weight:500; transition:.2s; box-shadow:0 2px 4px rgba(102,126,234,.3); }
.visit-order-item:hover { transform:translateX(4px); box-shadow:0 4px 8px rgba(102,126,234,.4); }
.visit-number { background:rgba(255,255,255,.9); color:#667eea; width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:.8rem; font-weight:700; margin-right:12px; flex-shrink:0; }
.stMetric { background:linear-gradient(135deg,#a8edea 0%,#fed6e3 100%); border:none; border-radius:12px; padding:16px 10px; text-align:center; transition:.2s; box-shadow:0 2px 4px rgba(168,237,234,.3); }
.stMetric:hover { transform:translateY(-2px); box-shadow:0 4px 8px rgba(168,237,234,.4); }
.empty-state { text-align:center; padding:40px 20px; color:#9ca3af; font-style:italic; font-size:.95rem; background:linear-gradient(135deg,#ffecd2 0%,#fcb69f 100%); border-radius:12px; margin:16px 0; }
div.element-container:has(#main_map), div[data-testid="stElement"]:has(#main_map), div[data-testid="stComponent"]:has(#main_map) { margin: 0 !important; padding: 0 !important; }
div[data-testid="stIFrame"]:has(> iframe), div[data-testid="stIFrame"] > iframe { margin: 0 !important; padding: 0 !important; border: none !important; }
#main_map .folium-map, #main_map .leaflet-container { width: 100% !important; height: 100% !important; margin: 0 !important; padding: 0 !important; }
.block-container { padding-top:1rem; padding-bottom:1rem; max-width:1400px; }
.stSuccess { background:linear-gradient(135deg,#d4edda 0%,#c3e6cb 100%); border:1px solid #b8dacd; border-radius:8px; color:#155724; }
.stWarning { background:linear-gradient(135deg,#fff3cd 0%,#ffeaa7 100%); border:1px solid #f8d7da; border-radius:8px; color:#856404; }
.stError { background:linear-gradient(135deg,#f8d7da 0%,#f5c6cb 100%); border:1px solid #f1b0b7; border-radius:8px; color:#721c24; }
</style>
""", unsafe_allow_html=True)

# ✅ 헤더
st.markdown('''
<div class="header-container">
    <img src="https://raw.githubusercontent.com/JeongWon4034/jeju/main/logo_jeju.png" alt='제주온 로고' style="width:125px; height:125px;">
    <div class="main-title">제주온 - 제주도 맞춤형 AI기반 스마트 관광 가이드</div>
</div>
<div class="title-underline"></div>
''', unsafe_allow_html=True)

# ✅ 여행 성향 선택
with st.container():
    st.markdown("### ✈️ 여행 성향 선택하기")
    st.write("원하는 여행 분위기나 목적을 선택하세요. AI가 이에 맞는 장소를 추천합니다.")
    travel_style = st.multiselect(
        "여행 키워드 선택 (최대 3개)",
        ["힐링","감성","자연","체험","커플","가족","액티비티","사진명소","카페투어","맛집탐방"],
        default=["힐링"]
    )
    if travel_style:
        st.success(f"선택한 여행 성향: {', '.join(travel_style)}")
    else:
        st.info("여행 성향을 하나 이상 선택해주세요.")
    show_recommend = st.button("🔍 AI 추천 보기", key="ai_recommend_button")

    if show_recommend:
        if not travel_style:
            st.warning("먼저 여행 성향을 선택해주세요!")
        else:
            try:
                base = "https://raw.githubusercontent.com/JeongWon4034/jeju/main/"
                fname = "비짓제주_이름기반_감성분석결과.csv"
                url = base + quote(fname)

                @st.cache_data
                def load_ai_recommendations(url_):
                    r = requests.get(url_, timeout=15)
                    r.raise_for_status()
                    r.encoding = "utf-8"
                    return pd.read_csv(io.StringIO(r.text))

                rec_df = load_ai_recommendations(url)
                st.success(f"선택한 성향({', '.join(travel_style)})에 맞는 추천지를 추렸어요 💫")

                pattern = "|".join(travel_style)
                filtered = rec_df[rec_df["최고추천성향"].astype(str).str.contains(pattern, na=False)]

                if filtered.empty:
                    st.error("해당 성향에 맞는 추천 결과가 없습니다 😢")
                else:
                    place_col = "관광지명" if "관광지명" in filtered.columns else filtered.columns[0]

                    filtered = (
                        filtered.sort_values(by="최고추천점수", ascending=False)
                                .drop_duplicates(subset=[place_col], keep="first")
                                .head(3)
                    )

                    for i, row in enumerate(filtered.to_dict("records"), 1):
                        title = row.get(place_col, "추천지")
                        style = row.get("최고추천성향", "")
                        score = row.get("최고추천점수", float("nan"))
                        cnt   = int(row.get("Cnt", 0)) if not pd.isna(row.get("Cnt", None)) else 0
                        link  = row.get("URL", "#")

                        st.markdown(f"""
                        <div style='background:linear-gradient(135deg,#fdfbfb 0%,#ebedee 100%);
                                    padding:16px;border-radius:12px;margin-bottom:12px;
                                    box-shadow:0 2px 5px rgba(0,0,0,0.05)'>
                            <h4 style='margin-bottom:4px'>🌟 {i}. {title}</h4>
                            <p style='margin:2px 0'>🧭 주요 성향: <b>{style}</b></p>
                            <p style='margin:2px 0'>💫 추천점수: <b>{score:.3f}</b></p>
                            <p style='margin:2px 0'>🔥 인기도(Cnt): {cnt}</p>
                            <a href='{link}' target='_blank'>🔗 자세히 보기</a>
                        </div>
                        """, unsafe_allow_html=True)
            except Exception as e:
                st.error("❌ 추천 데이터를 불러오는 중 오류가 발생했어요.")

# ✅ 메인 레이아웃
if data_loaded:
    col1, col2, col3 = st.columns([1.5, 1.2, 3], gap="large")
else:
    st.info("📌 데이터가 준비되면 경로 추천/지도가 활성화됩니다.")

# ✅ 경로/방문 순서/지도
if data_loaded:
    with col1:
        st.markdown('<div class="section-header">🚗 추천경로 설정</div>', unsafe_allow_html=True)
        st.markdown("**이동 모드**")
        mode = st.radio("", ["운전자", "도보"], horizontal=True, key="mode_key", label_visibility="collapsed")
        
        # 출발지 옵션: 기존 데이터 + final_result의 name_2
        start_options = list(gdf["사업장명"].dropna().unique())
        if restaurant_df is not None:
            tourist_spots = restaurant_df["name_2"].dropna().unique().tolist()
            start_options = sorted(list(set(start_options + tourist_spots)))
        
        st.markdown("**출발지**")
        start = st.selectbox("", start_options, key="start_key", label_visibility="collapsed")
        
        st.markdown("**경유지**")
        waypoint_options = [n for n in start_options if n != st.session_state.get("start_key", "")]
        wps = st.multiselect("", waypoint_options, key="wps_key", label_visibility="collapsed")
        
        c1, c2 = st.columns(2, gap="small")
        with c1:
            create_clicked = st.button("경로 생성")
        with c2:
            clear_clicked = st.button("초기화")

    if clear_clicked:
        try:
            for k in ["segments", "order", "selected_restaurants"]:
                st.session_state[k] = []
            for k in ["duration", "distance"]:
                st.session_state[k] = 0.0
            st.session_state["auto_gpt_input"] = ""
            for widget_key in ["mode_key", "start_key", "wps_key"]:
                if widget_key in st.session_state:
                    del st.session_state[widget_key]
            st.success("✅ 초기화가 완료되었습니다.")
            st.rerun()
        except Exception as e:
            st.error(f"❌ 초기화 중 오류: {str(e)}")

    if create_clicked:
        with st.spinner("최단거리 경로를 계산하고 있습니다..."):
            final_order, segments, duration, distance = calculate_shortest_route(start, wps, mode)
            
            if segments:
                st.session_state["order"] = final_order
                st.session_state["segments"] = segments
                st.session_state["duration"] = duration
                st.session_state["distance"] = distance
                
                # 맛집 관광지 리스트 저장
                selected_restaurant_spots = []
                for place in final_order:
                    if restaurant_df is not None:
                        if place in restaurant_df["name_2"].values:
                            selected_restaurant_spots.append(place)
                st.session_state["selected_restaurants"] = selected_restaurant_spots
                
                st.success("✅ 최단거리 경로가 생성되었습니다!")
                st.rerun()
            else:
                st.error("❌ 경로 생성에 실패했습니다. 다른 장소를 선택해주세요.")

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

    with col3:
        st.markdown('<div class="section-header">🗺️ 추천경로 지도시각화</div>', unsafe_allow_html=True)
        try:
            ctr = boundary.geometry.centroid
            clat, clon = float(ctr.y.mean()), float(ctr.x.mean())
            if math.isnan(clat) or math.isnan(clon):
                clat, clon = 33.38, 126.53
        except:
            clat, clon = 33.38, 126.53

        # 지도 렌더링
        try:
            m = folium.Map(
                location=[clat, clon],
                zoom_start=11,
                tiles="CartoDB Positron",
                prefer_canvas=True,
                control_scale=True
            )

            if boundary is not None:
                folium.GeoJson(
                    boundary,
                    style_function=lambda f: {"color": "#9aa0a6", "weight": 2, "dashArray": "4,4", "fillOpacity": 0.05}
                ).add_to(m)

            mc = MarkerCluster().add_to(m)

            # 기존 데이터 회색 마커 (백그라운드 - 마커클러스터)
            for _, row in gdf[gdf["type"].isin(["관광업", "음식점/카페"])].iterrows():
                if not (pd.isna(row.lat) or pd.isna(row.lon)):
                    folium.Marker(
                        [row.lat, row.lon],
                        popup=folium.Popup(str(row["사업장명"]), max_width=200),
                        tooltip=str(row["사업장명"]),
                        icon=folium.Icon(color="gray", icon="info-sign")
                    ).add_to(mc)

            # 자연경관 초록 마커
            try:
                natural_df = gdf[gdf["type"] == "자연경관"]
                for _, row in natural_df.iterrows():
                    if not (pd.isna(row.lat) or pd.isna(row.lon)):
                        parking = str(row.get("장애인주차여부", "정보 없음"))
                        toilet = str(row.get("화장실", "정보 없음"))
                        wheel = str(row.get("휠체어대여", "정보 없음"))
                        braille = str(row.get("점자표시판", "정보 없음"))
                        acc_url = str(row.get("열린광장url", ""))

                        popup_html = f"""
                        <b>{row['사업장명']}</b><br>
                        유형: 자연경관<br>
                        🚗 장애인주차: {parking}<br>
                        ♿ 휠체어대여: {wheel}<br>
                        🚻 화장실: {toilet}<br>
                        🔤 점자표시판: {braille}<br>
                        <a href="{acc_url}" target="_blank">🔗 접근성 상세보기</a>
                        """

                        folium.Marker(
                            [row.lat, row.lon],
                            popup=folium.Popup(popup_html, max_width=280),
                            tooltip=f"🌿 {row['사업장명']}",
                            icon=folium.Icon(color="green", icon="leaf")
                        ).add_to(m)
            except:
                pass

            # 선택된 관광지와 주변 맛집 표시
            selected_restaurant_spots = st.session_state.get("selected_restaurants", [])
            if restaurant_df is not None and selected_restaurant_spots:
                for spot in selected_restaurant_spots:
                    spot_data = restaurant_df[restaurant_df["name_2"] == spot]
                    if not spot_data.empty:
                        spot_row = spot_data.iloc[0]
                        # 관광지 마커 (파란색)
                        if not (pd.isna(spot_row["X_2"]) or pd.isna(spot_row["Y_2"])):
                            folium.Marker(
                                [spot_row["Y_2"], spot_row["X_2"]],
                                popup=folium.Popup(f"<b>🏛️ {spot}</b><br>관광지", max_width=200),
                                tooltip=f"🏛️ {spot}",
                                icon=folium.Icon(color="blue", icon="star")
                            ).add_to(m)
                        
                        # 주변 맛집 마커 (주황색)
                        restaurants = spot_data[["name_1", "X", "Y", "review", "p_n"]].drop_duplicates()
                        for _, rest in restaurants.iterrows():
                            if not (pd.isna(rest["X"]) or pd.isna(rest["Y"])):
                                sentiment = "긍정😊" if rest["p_n"] == "positive" else "부정😞" if rest["p_n"] == "negative" else "중립😐"
                                review_text = str(rest['review'])[:80] + "..." if len(str(rest['review'])) > 80 else str(rest['review'])
                                popup_html = f"""
                                <b>🍴 {rest['name_1']}</b><br>
                                <b>관광지:</b> {spot}<br>
                                <b>감정:</b> {sentiment}<br>
                                <b>리뷰:</b> {review_text}
                                """
                                folium.Marker(
                                    [rest["Y"], rest["X"]],
                                    popup=folium.Popup(popup_html, max_width=300),
                                    tooltip=f"🍴 {rest['name_1']}",
                                    icon=folium.Icon(color="orange", icon="cutlery")
                                ).add_to(m)

            # 경로선 그리기 (각 구간별로 다른 색상)
            if st.session_state.get("segments"):
                palette = ["#4285f4", "#34a853", "#ea4335", "#fbbc04", "#9c27b0", "#ff9800", "#00bcd4", "#ff5722"]
                segments = st.session_state["segments"]
                current_order = st.session_state.get("order", [])
                
                for i, seg in enumerate(segments):
                    if seg and len(seg) > 0:
                        # 경로선 그리기
                        folium.PolyLine(
                            [(pt[1], pt[0]) for pt in seg],
                            color=palette[i % len(palette)],
                            weight=6,
                            opacity=0.8
                        ).add_to(m)
                        
                        # 구간 번호 표시
                        mid_idx = len(seg) // 2
                        mid = seg[mid_idx]
                        folium.map.Marker(
                            [mid[1], mid[0]],
                            icon=DivIcon(
                                html=f"<div style='background:{palette[i % len(palette)]};"
                                     "color:#fff;border-radius:50%;width:32px;height:32px;"
                                     "line-height:32px;text-align:center;font-weight:700;font-size:14px;"
                                     "box-shadow:0 2px 6px rgba(0,0,0,0.4);'>"
                                     f"{i + 1}</div>"
                            )
                        ).add_to(m)
                
                # 경로 플래그 마커 (빨간색)
                for idx, place_name in enumerate(current_order, 1):
                    coord = get_coordinates(place_name)
                    if coord and not (pd.isna(coord[0]) or pd.isna(coord[1])):
                        folium.Marker(
                            [coord[1], coord[0]],
                            icon=folium.Icon(color="red", icon="flag"),
                            tooltip=f"{idx}. {place_name}",
                            popup=folium.Popup(f"<b>{idx}. {place_name}</b>", max_width=200)
                        ).add_to(m)
                
                # 지도 범위 조정
                try:
                    all_coords = []
                    for place in current_order:
                        coord = get_coordinates(place)
                        if coord and not (pd.isna(coord[0]) or pd.isna(coord[1])):
                            all_coords.append([coord[1], coord[0]])
                    
                    if all_coords:
                        m.fit_bounds(all_coords, padding=[50, 50])
                except:
                    m.location = [clat, clon]
                    m.zoom_start = 11
            else:
                m.location = [clat, clon]
                m.zoom_start = 11

            st_folium(m, key="main_map", width=None, height=520, returned_objects=[], use_container_width=True)

        except Exception as map_error:
            st.error(f"❌ 지도 렌더링 오류: {str(map_error)}")

# ✅ OpenAI 클라이언트
client = openai.OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# ✅ 생성형 AI 가이드
st.markdown("---")
st.markdown('<div class="section-header">🤖 생성형 AI기반 관광 가이드</div>', unsafe_allow_html=True)

if st.button("🔁 방문 순서 자동 입력"):
    st.session_state["auto_gpt_input"] = ", ".join(st.session_state.get("order", []))

if "messages" not in st.session_state:
    st.session_state["messages"] = []

with st.form("chat_form"):
    user_input = st.text_input(
        "관광지명을 쉼표로 구분해서 입력하거나 궁금한 것을 물어보세요 !",
        value=st.session_state.get("auto_gpt_input", "")
    )
    submitted = st.form_submit_button("🔍 관광지 정보 요청")

if submitted and user_input and client is not None:
    if st.session_state["order"]:
        st.markdown("---")
        st.markdown("## ✨ 관광지별 상세 정보")
        for place in st.session_state["order"][:3]:
            try:
                matched = data[data['t_name'].str.contains(place, na=False)]
            except:
                matched = pd.DataFrame()

            # GPT 소개
            try:
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "당신은 제주 지역의 관광지 및 카페, 식당을 간단하게 소개하는 관광 가이드입니다."},
                        {"role": "system", "content": "존댓말을 사용하세요."},
                        {"role": "user", "content": f"{place}를 두 문단 이내로 간단히 설명해주세요."}
                    ]
                )
                gpt_intro = response.choices[0].message.content
            except Exception as e:
                gpt_intro = f"❌ GPT 호출 실패: {place} 소개를 불러올 수 없어요."

            score_text = ""; review_block = ""; cafe_info = ""
            if not matched.empty:
                try:
                    t_value = matched['t_value'].dropna().unique()
                    score_text = f"📊**관광지 평점**: ⭐ {t_value[0]}" if len(t_value) > 0 else ""
                    reviews = matched['t_review'].dropna().unique()
                    reviews = [r for r in reviews if all(x not in str(r) for x in ["없음", "없읍"])]
                    if reviews:
                        review_block = "\n".join([f'"{r}"' for r in reviews[:3]])
                    cafes = matched[['c_name', 'c_value', 'c_review']].drop_duplicates()
                    cafe_info = format_cafes(cafes)
                except:
                    cafe_info = ""

            # 맛집 정보 추가 (final_result.csv 기반)
            restaurant_info = ""
            if restaurant_df is not None:
                rest_matched = restaurant_df[restaurant_df["name_2"] == place]
                if not rest_matched.empty:
                    restaurant_info = "#### 🍴 주변 맛집 추천\n\n"
                    rest_grouped = rest_matched.groupby("name_1")
                    for name, group in list(rest_grouped)[:5]:
                        reviews = group["review"].dropna().tolist()
                        sentiments = group["p_n"].value_counts()
                        positive_cnt = sentiments.get("positive", 0)
                        negative_cnt = sentiments.get("negative", 0)
                        sentiment_text = f"긍정 {positive_cnt}개" if positive_cnt > 0 else ""
                        if negative_cnt > 0:
                            sentiment_text += f", 부정 {negative_cnt}개" if sentiment_text else f"부정 {negative_cnt}개"
                        
                        restaurant_info += f"**{name}** ({sentiment_text})\n\n"
                        if reviews:
                            for rev in reviews[:2]:
                                restaurant_info += f"- \"{rev}\"\n"
                        restaurant_info += "\n"

            st.markdown(f"### 🏛️ {place}")
            if score_text:
                st.markdown(score_text)
            st.markdown("#### ✨ 소개")
            st.markdown(gpt_intro.strip())
            if restaurant_info:
                st.markdown(restaurant_info.strip())
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
