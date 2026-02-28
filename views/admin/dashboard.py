"""
管理员仪表盘：名单概览指标、专业与年份分布图、近期变动。
"""
import pandas as pd
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots

from sqlalchemy import func

from core.config import EMPTY_NO_RECORDS
from core.database import db_session
from core.models import Blacklist

DASHBOARD_CACHE_TTL = 60


@st.cache_data(ttl=DASHBOARD_CACHE_TTL)
def get_dashboard_counts():
    """仪表盘三指标：(total, effective, revoked)，单次 GROUP BY 查询。"""
    with db_session() as db:
        rows = db.query(Blacklist.status, func.count()).group_by(Blacklist.status).all()
    counts = dict(rows)
    effective = counts.get(1, 0)
    revoked = counts.get(0, 0)
    return effective + revoked, effective, revoked


@st.cache_data(ttl=DASHBOARD_CACHE_TTL)
def get_dashboard_major_counts():
    """专业分布：返回 [(专业名, 人数), ...]。"""
    with db_session() as db:
        rows = db.query(Blacklist.major).filter(Blacklist.status == 1).all()
    if not rows:
        return []
    major_series = pd.Series([r[0] or "未填写" for r in rows])
    counts = major_series.value_counts().reset_index()
    return [(str(m), int(c)) for m, c in zip(counts.iloc[:, 0].tolist(), counts.iloc[:, 1].tolist())]


@st.cache_data(ttl=DASHBOARD_CACHE_TTL)
def get_dashboard_year_counts():
    """按处分年份分布：返回 [(年份, 人数), ...] 已按年份排序。"""
    with db_session() as db:
        date_rows = db.query(Blacklist.punishment_date).filter(Blacklist.status == 1).all()
    if not date_rows or all(r[0] is None for r in date_rows):
        return []
    years = [r[0].year for r in date_rows if r[0]]
    if not years:
        return []
    year_series = pd.Series(years)
    year_counts = year_series.value_counts().sort_index().reset_index()
    return [(int(y), int(c)) for y, c in zip(year_counts.iloc[:, 0].tolist(), year_counts.iloc[:, 1].tolist())]


@st.cache_resource(ttl=DASHBOARD_CACHE_TTL)
def _get_dashboard_combined_figure(major_counts_tuple, year_counts_tuple):
    """专业分布饼图与年份柱状图合并为单图并缓存。"""
    fig = make_subplots(
        rows=1, cols=2,
        specs=[[{"type": "pie"}, {"type": "bar"}]],
        subplot_titles=("专业分布", "按处分年份分布"),
    )
    if major_counts_tuple:
        counts_df = pd.DataFrame(list(major_counts_tuple), columns=["专业", "人数"])
        fig_pie = px.pie(counts_df, values="人数", names="专业")
        fig.add_trace(fig_pie.data[0], row=1, col=1)
    if year_counts_tuple:
        year_df = pd.DataFrame(list(year_counts_tuple), columns=["年份", "人数"])
        fig_bar = px.bar(year_df, x="年份", y="人数")
        fig.add_trace(fig_bar.data[0], row=1, col=2)
    if not major_counts_tuple and not year_counts_tuple:
        return None
    fig.update_layout(height=320, margin={"l": 20, "r": 20, "t": 50, "b": 20}, showlegend=True)
    fig.update_xaxes(title_text="年份", row=1, col=2)
    fig.update_yaxes(title_text="人数", row=1, col=2)
    return fig


@st.cache_data(ttl=DASHBOARD_CACHE_TTL)
def get_recent_blacklist_rows():
    """近期名单变动：返回最多 10 条。"""
    with db_session() as db:
        recent = db.query(Blacklist).order_by(Blacklist.created_at.desc()).limit(10).all()
    return [
        {
            "姓名": r.name,
            "学号": r.student_id,
            "专业": (r.major or "")[:20],
            "状态": "生效" if r.status == 1 else "已撤销",
            "创建/更新": str(r.created_at)[:19] if r.created_at else "",
        }
        for r in recent
    ]


def _render_dashboard_metrics():
    total, effective, revoked = get_dashboard_counts()
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("名单总数", total)
    with col2:
        st.metric("生效中", effective)
    with col3:
        st.metric("已撤销", revoked)


def _render_dashboard_charts():
    major_counts = get_dashboard_major_counts()
    year_counts = get_dashboard_year_counts()
    if not major_counts and not year_counts:
        st.caption("暂无生效记录或处分日期数据，无法生成分布图。")
        return
    fig = _get_dashboard_combined_figure(
        tuple((m, c) for m, c in major_counts) if major_counts else (),
        tuple((y, c) for y, c in year_counts) if year_counts else (),
    )
    if fig is not None:
        st.plotly_chart(fig, use_container_width=False, key="admin_dashboard_charts")


def _render_dashboard_recent():
    st.subheader("近期名单变动")
    st.caption("最近录入或更新的名单记录，便于核对与追溯。")
    try:
        rows = get_recent_blacklist_rows()
        if not rows:
            st.caption(EMPTY_NO_RECORDS)
        else:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    except Exception:
        st.caption("加载近期变动失败。")


def _render_dashboard():
    st.caption("名单总数与生效/撤销概况、专业与年份分布、近期名单变动及操作统计，便于快速把握现状。")
    _render_dashboard_metrics()
    _render_dashboard_charts()
    _render_dashboard_recent()


@st.fragment
def _dashboard_fragment():
    """仪表盘以 fragment 包裹，仅仪表盘内交互时局部重跑。"""
    _render_dashboard()
