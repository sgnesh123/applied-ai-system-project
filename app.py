from datetime import date, time

import streamlit as st
from dotenv import load_dotenv

from ai_suggester import LOG_FILE, MAX_NAME_LENGTH, setup_logging, suggest_tasks
from llm_client import MockClient, get_client
from pawpal_system import CATEGORIES, Owner, Pet, Scheduler, Task, format_time

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

load_dotenv()
setup_logging()

st.title("🐾 PawPal+")

st.markdown(
    """
PawPal+ helps busy pet owners stay on top of pet care. Add your pets and their care tasks,
tell it how much time you have today, and it builds a daily schedule and explains its choices.
"""
)

with st.expander("How it works"):
    st.markdown(
        """
1. **Set your day:** enter how many minutes you have, when your day starts, and any
   categories (like meds or feeding) that should come first when priorities are tied.
2. **Add pets and tasks:** each task has a duration, priority, category, and frequency
   (daily or weekly). Pin a task to a time if it has to happen then.
3. **Generate a schedule:** PawPal+ keeps the tasks due today, orders them by priority,
   and skips any that don't fit your time. Pinned tasks stay at their set time, and the
   rest fill the open gaps.
4. **Read the reasoning:** each task shows why it was scheduled. Skipped tasks and
   overlapping pinned tasks are flagged.
"""
    )

st.divider()

# Keep one Owner across Streamlit reruns so added pets and tasks aren't lost.
if "owner" not in st.session_state:
    st.session_state.owner = Owner(name="Jordan")
owner = st.session_state.owner

st.subheader("Owner")
col1, col2, col3 = st.columns(3)
with col1:
    owner.name = st.text_input("Owner name", value="Jordan")
with col2:
    owner.available_minutes = int(
        st.number_input("Minutes available today", min_value=0, max_value=1440, value=120, step=5)
    )
with col3:
    start = st.time_input("Day starts at", value=time(8, 0))
    owner.day_start = start.hour * 60 + start.minute
owner.preferred_categories = st.multiselect(
    "Preferred categories (break ties between equal priorities)", CATEGORIES
)

st.subheader("Add a Pet")
with st.form("add_pet", clear_on_submit=True):
    col1, col2, col3 = st.columns(3)
    with col1:
        pet_name = st.text_input("Pet name", max_chars=MAX_NAME_LENGTH)
    with col2:
        species = st.selectbox("Species", ["dog", "cat", "other"])
    with col3:
        age = st.number_input("Age", min_value=0, max_value=40, value=1)
    if st.form_submit_button("Add pet"):
        if pet_name.strip():
            owner.add_pet(Pet(name=pet_name.strip(), species=species, age=int(age)))
            st.success(f"Added {pet_name.strip()}.")
        else:
            st.error("Please enter a pet name.")

if owner.pets:
    st.caption("Pets: " + ", ".join(f"{pet.name} ({pet.species})" for pet in owner.pets))
else:
    st.info("No pets yet. Add one above.")

st.subheader("✨ AI Task Suggestions")
st.caption(
    "Pick a pet and PawPal+ looks up care notes for its species and age, then asks the AI to "
    "suggest tasks based on those notes. Review the suggestions before adding them."
)
st.warning(
    "⚠️ Suggestions are general care guidance, not veterinary advice. For medications, health "
    "problems, or anything your vet has told you, follow your vet's instructions.",
    icon=None,
)
if owner.pets:
    client = get_client()
    if isinstance(client, MockClient):
        st.info("Offline mode: no GEMINI_API_KEY found, so default tasks will be suggested. See README for setup.")

    ai_pet_index = st.selectbox(
        "Suggest tasks for",
        range(len(owner.pets)),
        format_func=lambda i: f"{owner.pets[i].name} ({owner.pets[i].species}, age {owner.pets[i].age})",
        key="ai_pet_index",
    )
    if "ai_flash" in st.session_state:
        st.success(st.session_state.pop("ai_flash"))
    if st.button("Suggest tasks"):
        with st.spinner("Retrieving care notes and asking the AI..."):
            st.session_state.ai_result = suggest_tasks(owner.pets[ai_pet_index], owner, client)
            st.session_state.ai_result_pet = ai_pet_index

    result = st.session_state.get("ai_result")
    if result is not None and st.session_state.get("ai_result_pet") == ai_pet_index:
        ai_pet = owner.pets[ai_pet_index]
        if result.used_fallback:
            st.warning("No valid AI suggestions, so these are default tasks for this species.")
        else:
            st.success(f"{len(result.suggestions)} suggestions for {ai_pet.name}, based on the care notes below.")

        edited = st.data_editor(
            [
                {
                    "Accept": True,
                    "Task": s.task.description,
                    "Minutes": s.task.duration_minutes,
                    "Priority": s.task.priority,
                    "Category": s.task.category,
                    "Frequency": s.task.frequency,
                    "Pinned at": format_time(s.task.preferred_start) if s.task.preferred_start is not None else "-",
                    "Source": s.source,
                    "Why": s.reason,
                }
                for s in result.suggestions
            ],
            column_config={
                "Accept": st.column_config.CheckboxColumn(),
                "Minutes": st.column_config.NumberColumn(min_value=1, max_value=240, step=1),
                "Priority": st.column_config.SelectboxColumn(options=["low", "medium", "high"]),
            },
            disabled=["Task", "Category", "Frequency", "Pinned at", "Source", "Why"],
            hide_index=True,
            key=f"ai_editor_{id(result)}",
        )

        if st.button("Add accepted tasks"):
            added = 0
            for suggestion, row in zip(result.suggestions, edited):
                if row["Accept"]:
                    suggestion.task.duration_minutes = int(row["Minutes"])
                    suggestion.task.priority = row["Priority"]
                    ai_pet.add_task(suggestion.task)
                    added += 1
            st.session_state.ai_result = None
            st.session_state.ai_flash = f"Added {added} AI-suggested task(s) for {ai_pet.name}."
            st.rerun()

        with st.expander("Retrieved care notes (what the AI was given)"):
            if result.notes:
                for name, text in result.notes.items():
                    st.markdown(f"**{name}**")
                    st.text(text)
            else:
                st.write("No notes found.")
        if result.rejected:
            with st.expander(f"Rejected by validator ({len(result.rejected)})"):
                for reason in result.rejected:
                    st.write(f"- {reason}")
else:
    st.caption("Add a pet first, then PawPal+ can suggest tasks for it.")

with st.expander("AI activity log"):
    try:
        lines = LOG_FILE.read_text(encoding="utf-8").splitlines()[-30:]
        st.text("\n".join(lines) or "No activity yet.")
    except FileNotFoundError:
        st.text("No activity yet.")

st.subheader("Schedule a Task")
if owner.pets:
    with st.form("add_task", clear_on_submit=True):
        pet_index = st.selectbox(
            "Pet", range(len(owner.pets)), format_func=lambda i: f"{owner.pets[i].name} ({owner.pets[i].species})"
        )
        pet = owner.pets[pet_index]
        col1, col2, col3 = st.columns(3)
        with col1:
            description = st.text_input("Task", value="Morning walk")
        with col2:
            duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
        with col3:
            priority = st.selectbox("Priority", ["low", "medium", "high"], index=2)
        col1, col2, col3 = st.columns(3)
        with col1:
            category = st.selectbox("Category", CATEGORIES)
        with col2:
            frequency = st.selectbox("Frequency", ["daily", "weekly"])
        with col3:
            pinned_time = st.time_input("Preferred time", value=time(8, 0))
        pin = st.checkbox("Pin this task to the preferred time")
        if st.form_submit_button("Add task"):
            if description.strip():
                pet.add_task(
                    Task(
                        description=description.strip(),
                        duration_minutes=int(duration),
                        priority=priority,
                        category=category,
                        frequency=frequency,
                        preferred_start=pinned_time.hour * 60 + pinned_time.minute if pin else None,
                    )
                )
                st.success(f"Added {description.strip()} for {pet.name}.")
            else:
                st.error("Please enter a task description.")
else:
    st.caption("Add a pet first, then you can schedule tasks for it.")

all_tasks = owner.get_all_tasks()
if all_tasks:
    st.write("Current tasks:")
    st.table(
        [
            {
                "Pet": pet.name,
                "Task": task.description,
                "Minutes": task.duration_minutes,
                "Priority": task.priority,
                "Category": task.category,
                "Frequency": task.frequency,
                "Pinned at": format_time(task.preferred_start) if task.preferred_start is not None else "-",
            }
            for pet, task in all_tasks
        ]
    )

st.divider()

st.subheader("Build Schedule")

if st.button("Generate schedule"):
    if not all_tasks:
        st.warning("Add at least one pet and one task first.")
    else:
        scheduler = Scheduler(owner)
        scheduler.generate_plan(date.today())

        used = sum(task.duration_minutes for _, _, task, _ in scheduler.plan)
        st.success(
            f"Today's schedule for {owner.name}: {len(scheduler.plan)} tasks, "
            f"{used} of {owner.available_minutes} minutes used."
        )
        if scheduler.plan:
            st.table(
                [
                    {
                        "Time": f"{format_time(start)}-{format_time(start + task.duration_minutes)}",
                        "Pet": pet.name,
                        "Task": task.description,
                        "Why": reason,
                    }
                    for start, pet, task, reason in scheduler.plan
                ]
            )

        if scheduler.skipped:
            st.warning(
                "Skipped (not enough time left): "
                + ", ".join(f"{pet.name}: {task.description} ({task.duration_minutes} min)" for pet, task in scheduler.skipped)
            )

        for warning in scheduler.detect_conflicts():
            st.error(f"Conflict: {warning}")

        with st.expander("Full explanation"):
            st.text(scheduler.explain_plan())
