from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List

import streamlit as st
from streamlit_autorefresh import st_autorefresh


FIB_VALUES: List[str] = ["1", "2", "3", "5", "8", "13", "21", "cup"]
STALE_SECONDS = 45
REFRESH_MS = 1500


@dataclass
class Participant:
    name: str
    estimate: Optional[str]
    last_seen: float


class Store:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.participants: Dict[str, Participant] = {}  # session_id -> Participant
        self.reveal: bool = False
        self.session_active: bool = False
        self.scrum_master_session_id: Optional[str] = None


@st.cache_resource
def get_store() -> Store:
    return Store()


def _now() -> float:
    return time.time()


def _prune_stale(store: Store) -> None:
    # Backwards-compat for older cached Store instances on Streamlit Cloud
    # that may not yet have the newer attributes.
    if not hasattr(store, "session_active"):
        store.session_active = False
    if not hasattr(store, "reveal"):
        store.reveal = False
    if not hasattr(store, "scrum_master_session_id"):
        store.scrum_master_session_id = None

    cutoff = _now() - STALE_SECONDS
    stale_ids = [sid for sid, p in store.participants.items() if p.last_seen < cutoff]
    for sid in stale_ids:
        del store.participants[sid]
        if store.scrum_master_session_id == sid:
            store.scrum_master_session_id = None
            store.session_active = False
            store.reveal = False


def touch_participant(store: Store, session_id: str, name: str) -> None:
    name = name.strip()
    if not name:
        return

    with store.lock:
        _prune_stale(store)
        if session_id not in store.participants:
            store.participants[session_id] = Participant(name=name, estimate=None, last_seen=_now())
        else:
            p = store.participants[session_id]
            p.name = name
            p.last_seen = _now()

        # Ensure only one entry per name (case-insensitive) across all sessions
        duplicate_ids = [
            sid
            for sid, participant in store.participants.items()
            if sid != session_id and participant.name.strip().lower() == name.lower()
        ]
        for dup_id in duplicate_ids:
            if store.scrum_master_session_id == dup_id:
                # Transfer Scrum Master role to the latest active session for this name
                store.scrum_master_session_id = session_id
            del store.participants[dup_id]


def set_estimate(store: Store, session_id: str, value: str) -> None:
    if value not in FIB_VALUES:
        return

    with store.lock:
        _prune_stale(store)
        if (not store.session_active) or store.reveal:
            return
        if session_id in store.participants:
            store.participants[session_id].estimate = value
            store.participants[session_id].last_seen = _now()


def reset_story(store: Store) -> None:
    with store.lock:
        _prune_stale(store)
        store.reveal = False
        for p in store.participants.values():
            p.estimate = None


def reveal_all(store: Store) -> None:
    with store.lock:
        _prune_stale(store)
        if not store.session_active:
            return
        store.reveal = True


def start_session(store: Store) -> None:
    with store.lock:
        _prune_stale(store)
        if store.scrum_master_session_id is None:
            return
        store.session_active = True
        store.reveal = False
        for p in store.participants.values():
            p.estimate = None


def end_session(store: Store) -> None:
    with store.lock:
        _prune_stale(store)
        store.session_active = False
        store.reveal = False
        for p in store.participants.values():
            p.estimate = None


def become_scrum_master(store: Store, session_id: str) -> None:
    with store.lock:
        _prune_stale(store)
        if store.scrum_master_session_id is None:
            store.scrum_master_session_id = session_id
            store.session_active = False
            store.reveal = False


def get_state_snapshot(store: Store) -> Tuple[bool, bool, Optional[str], List[Tuple[str, Participant]]]:
    with store.lock:
        _prune_stale(store)
        reveal = store.reveal
        session_active = store.session_active and (store.scrum_master_session_id is not None)
        if not session_active:
            store.session_active = False
            store.reveal = False
        scrum_master_session_id = store.scrum_master_session_id
        items = sorted(store.participants.items(), key=lambda kv: kv[1].name.lower())
        return reveal, session_active, scrum_master_session_id, items


def display_value(v: Optional[str]) -> str:
    if v is None:
        return "-"
    return "☕" if v == "cup" else v


st.set_page_config(page_title="Sprint Story Estimation", page_icon="☕", layout="wide")

# Global UI tweaks (cards-style buttons, slightly nicer typography)
st.markdown(
    """
    <style>
    div.stButton > button {
        border-radius: 0.7rem;
        padding: 0.65rem 0.95rem;
        border: 1px solid #d1d5db;
        background: #ffffff;
        color: #111827;
        font-weight: 600;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.15);
        transition: all 0.12s ease-in-out;
    }
    div.stButton > button:hover {
        box-shadow: 0 4px 10px rgba(15, 23, 42, 0.20);
        transform: translateY(-1px);
        border-color: #2563eb;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Auto-refresh to simulate realtime updates across connected users
st_autorefresh(interval=REFRESH_MS, key="auto_refresh")

store = get_store()

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "joined" not in st.session_state:
    st.session_state.joined = False
if "name" not in st.session_state:
    st.session_state.name = ""
if "selected" not in st.session_state:
    st.session_state.selected = None

session_id: str = st.session_state.session_id

st.title("Sprint Story Estimation")

if not st.session_state.joined:
    st.subheader("Join session")
    name_input = st.text_input("Your name", value=st.session_state.name, max_chars=40)
    if st.button("Join"):
        cleaned = name_input.strip()
        if cleaned:
            st.session_state.name = cleaned
            st.session_state.joined = True
            touch_participant(store, session_id, cleaned)
            st.rerun()

    st.stop()

# Joined path
name = st.session_state.name
if not name.strip():
    st.session_state.joined = False
    st.rerun()

touch_participant(store, session_id, name)

reveal, session_active, scrum_master_sid, participants = get_state_snapshot(store)

is_scrum_master = scrum_master_sid == session_id

header_left, header_right = st.columns([2, 1])
with header_left:
    st.markdown(f"**You are:** {name}")
    st.caption(f"Session: {'Active' if session_active else 'Not started'}")
with header_right:
    if scrum_master_sid is None:
        if st.button("Become Scrum Master", use_container_width=True):
            become_scrum_master(store, session_id)
            st.rerun()
    else:
        scrum_master_name = next((p.name for sid, p in participants if sid == scrum_master_sid), "(unknown)")
        st.markdown(f"**Scrum Master:** {scrum_master_name}")

if is_scrum_master:
    c1, _ = st.columns(2)
    with c1:
        if st.button("Start Session", use_container_width=True, disabled=session_active):
            start_session(store)
            st.session_state.selected = None
            st.rerun()

    c3, c4 = st.columns(2)
    with c3:
        if st.button("Reveal", use_container_width=True, disabled=not session_active):
            reveal_all(store)
            st.rerun()
    with c4:
        if st.button("Reset", use_container_width=True, disabled=not session_active):
            reset_story(store)
            st.session_state.selected = None
            st.rerun()
else:
    if not session_active:
        st.info("Session has not started yet. Waiting for Scrum Master.")
    elif not reveal:
        st.info("Pick your estimate. Scores stay hidden until Scrum Master reveals.")
    else:
        st.info("Scrum Master revealed the scores.")

left, right = st.columns([2, 1])

with left:
    st.subheader("Choose your estimate")

    grid = st.columns(4)
    for i, v in enumerate(FIB_VALUES):
        col = grid[i % 4]
        label = "☕" if v == "cup" else v
        with col:
            if st.button(
                label,
                key=f"card_{v}",
                use_container_width=True,
                disabled=(not session_active) or reveal,
            ):
                st.session_state.selected = v
                set_estimate(store, session_id, v)

    if st.session_state.selected is not None:
        st.caption(f"Selected: {display_value(st.session_state.selected)} (hidden until reveal)")

with right:
    st.subheader("Participants")

    reveal, session_active, scrum_master_sid, participants = get_state_snapshot(store)

    waiting_count = 0
    for sid, p in participants:
        if p.estimate is None:
            waiting_count += 1

    st.caption(f"Waiting: {waiting_count} / {len(participants)}")

    if not participants:
        st.write("No participants yet.")
    else:
        # Sort so people who haven't estimated appear first
        sorted_participants = sorted(
            participants,
            key=lambda item: (item[1].estimate is not None, item[1].name.lower()),
        )

        for sid, p in sorted_participants:
            row = st.columns([2, 1])
            row[0].markdown(f"**{p.name}**")

            if p.estimate is None:
                dot_color = "#f97373"  # red
                label_html = "Waiting…"
                row[1].markdown(
                    f"<span style='display:inline-flex;align-items:center;'>"
                    f"<span style='width:10px;height:10px;border-radius:999px;background:{dot_color};margin-right:6px;'></span>"
                    f"<span style='font-size:0.95rem;'>Waiting…</span>"
                    f"</span>",
                    unsafe_allow_html=True,
                )
            else:
                if not reveal:
                    dot_color = "#facc15"  # amber
                    row[1].markdown(
                        f"<span style='display:inline-flex;align-items:center;'>"
                        f"<span style='width:10px;height:10px;border-radius:999px;background:{dot_color};margin-right:6px;'></span>"
                        f"<span style='font-size:0.95rem;'>Estimated</span>"
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    dot_color = "#4ade80"  # green
                    row[1].markdown(
                        f"<span style='display:inline-flex;align-items:center;'>"
                        f"<span style='width:10px;height:10px;border-radius:999px;background:{dot_color};margin-right:6px;'></span>"
                        f"<span style='font-size:1.4rem;font-weight:700;'>{display_value(p.estimate)}</span>"
                        f"</span>",
                        unsafe_allow_html=True,
                    )

        # Show summary only after scores are revealed
        if reveal:
            numeric_values = []
            counts = {v: 0 for v in FIB_VALUES}
            for _, p in participants:
                if p.estimate is None:
                    continue
                if p.estimate in counts:
                    counts[p.estimate] += 1
                # Only numeric values contribute to the average
                if p.estimate.isdigit():
                    try:
                        numeric_values.append(int(p.estimate))
                    except ValueError:
                        pass

            st.markdown("---")
            st.subheader("Results summary")

            avg_col, _ = st.columns(2)
            with avg_col:
                if numeric_values:
                    avg_value = sum(numeric_values) / len(numeric_values)
                    st.metric("Average story points", f"{avg_value:.2f}")
                else:
                    st.metric("Average story points", "–")

            st.markdown("**Counts per score**")
            cols = st.columns(4)
            for idx, key in enumerate(FIB_VALUES):
                col = cols[idx % 4]
                label = "☕" if key == "cup" else key
                with col:
                    st.markdown(
                        f"<div style='font-size:1.1rem;'><strong>{label}</strong>: {counts[key]}</div>",
                        unsafe_allow_html=True,
                    )
