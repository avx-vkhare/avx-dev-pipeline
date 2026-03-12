"""
Browser-based chat UI for the dev pipeline.

Run:  /usr/bin/python3 ui.py
Then open http://127.0.0.1:7860
"""

import queue
import threading
import anyio
print("[ui] importing gradio...", flush=True)
import gradio as gr
print(f"[ui] gradio {gr.__version__} loaded", flush=True)

print("[ui] importing pipeline...", flush=True)
from context import ProjectContext
from pipeline import run_pipeline
print("[ui] pipeline imported ok", flush=True)

# ---------------------------------------------------------------------------
# Thread-safe queues to bridge pipeline ↔ UI
# ---------------------------------------------------------------------------
output_q: queue.Queue = queue.Queue()
input_q:  queue.Queue = queue.Queue()
pipeline_thread: threading.Thread | None = None


def emit_fn(msg: str):
    s = str(msg).strip()
    # Tool-level status — goes to status bar only, not chat
    if s.startswith("__status__"):
        output_q.put(("status", s[len("__status__"):]))
        return
    # Stage headers
    for keyword, stage in [
        ("PLANNER",     "plan"),
        ("CODER",       "code"),
        ("TEST WRITER", "test"),
        ("REVIEWER",    "review"),
    ]:
        if keyword in s and "=" * 10 in s:
            output_q.put(("stage", stage))
            break
    output_q.put(("msg", s))


def ask_fn(prompt: str) -> str:
    output_q.put(("ask", prompt))
    return input_q.get()


def run_pipeline_in_thread(ctx: ProjectContext):
    try:
        anyio.run(run_pipeline, ctx, ask_fn, emit_fn)
    except SystemExit as e:
        output_q.put(("msg", f"Pipeline stopped: {e}"))
    except Exception as e:
        output_q.put(("msg", f"Pipeline error: {e}"))
    finally:
        output_q.put(("done", ""))


# ---------------------------------------------------------------------------
# Progress bar HTML
# ---------------------------------------------------------------------------
STAGE_ORDER  = ["plan", "code", "test", "review", "done"]
STAGE_LABELS = {"plan": "📋 Plan", "code": "💻 Code", "test": "🧪 Tests",
                "review": "🔍 Review", "done": "✅ Done"}

def progress_html(active: str, spinning: bool) -> str:
    parts = []
    reached = False
    for s in STAGE_ORDER:
        if s == active:
            reached = True
        label = STAGE_LABELS[s]
        if s == active and spinning and s != "done":
            style = "background:#f59e0b;color:white;font-weight:bold;padding:6px 14px;border-radius:20px;"
            spin  = ' <span style="display:inline-block;animation:spin 1s linear infinite">⟳</span>'
            parts.append(f'<span style="{style}">{label}{spin}</span>')
        elif s == active:
            style = "background:#3b82f6;color:white;font-weight:bold;padding:6px 14px;border-radius:20px;"
            parts.append(f'<span style="{style}">{label}</span>')
        elif not reached:
            style = "background:#6ee7b7;color:#065f46;padding:6px 14px;border-radius:20px;"
            parts.append(f'<span style="{style}">{label}</span>')
        else:
            style = "background:#e5e7eb;color:#9ca3af;padding:6px 14px;border-radius:20px;"
            parts.append(f'<span style="{style}">{label}</span>')
    arrow = ' <span style="color:#9ca3af;margin:0 4px">→</span> '
    return (
        '<style>@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}</style>'
        f'<div style="padding:10px 0;display:flex;align-items:center;flex-wrap:wrap;gap:4px">'
        + arrow.join(parts) + '</div>'
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
DEFAULT_TESTS = "\n".join([
    "bazel test //go/aviatrix.com/conduit/v2/gateway-conduit:gateway-conduit_test",
    "bazel test //go/aviatrix.com/launcher/launchertest:launchertest_etcd_test",
    "bazel test //go/aviatrix.com/launcher/gateway_launcher:gateway_launcher_test",
    "bazel test //go/aviatrix.com/conduit/v2/controller-conduit:controller-conduit_test",
])
DEFAULT_LINT = "\n".join(["make lint-standard", "make lint-avx", "make lint-strict"])

with gr.Blocks(title="Dev Pipeline") as demo:
    gr.Markdown("# Dev Pipeline")
    progress_bar = gr.HTML(value=progress_html("plan", False))

    # ---- Config form -------------------------------------------------------
    with gr.Accordion("⚙️  Task Configuration", open=True) as config_panel:
        with gr.Row():
            fi_ticket = gr.Textbox(label="Jira Ticket ID", placeholder="AVX-73843", scale=1)
            fi_branch = gr.Textbox(label="Branch Name",    placeholder="AVX-73843-my-fix", scale=1)
            fi_lang   = gr.Dropdown(
                label="Language",
                choices=["Go", "Python", "Go and Python"],
                value="Go", scale=1,
            )
        fi_task = gr.Textbox(
            label="Task Description",
            placeholder="Plain English description of what needs to be done",
            lines=2,
        )
        fi_repo = gr.Textbox(
            label="Repo Path",
            value="/home/vkhare/cloudn",
        )
        with gr.Row():
            fi_tests = gr.Textbox(
                label="Test Commands (one per line)",
                value=DEFAULT_TESTS,
                lines=4, scale=2,
            )
            fi_lint = gr.Textbox(
                label="Lint Commands (one per line)",
                value=DEFAULT_LINT,
                lines=4, scale=1,
            )

    # ---- Main area: action log + chat side by side -------------------------
    with gr.Row():
        action_bar = gr.Textbox(
            value="", label="🔧 Live Actions", interactive=False,
            lines=36, max_lines=36, scale=3,
        )
        with gr.Column(scale=2):
            chatbot = gr.Chatbot(label="Pipeline output", height=660)
            with gr.Row():
                msg_box = gr.Textbox(
                    placeholder="Type your response and press Enter...",
                    show_label=False, scale=9, interactive=False,
                )
                send_btn = gr.Button("Send", scale=1, interactive=False)

    with gr.Row():
        start_btn = gr.Button("▶ Start Pipeline", variant="primary")
        status    = gr.Textbox(value="Idle", label="Status", interactive=False, scale=3)

    waiting    = gr.State(False)
    cur_stage  = gr.State("plan")
    action_log = gr.State("")

    # ---- Handlers ----------------------------------------------------------
    def start_pipeline(ticket, branch, lang, task, repo, tests_raw, lint_raw):
        global pipeline_thread, output_q, input_q

        # Validate required fields
        if not ticket.strip() or not task.strip() or not branch.strip():
            return (
                gr.update(),                              # chatbot
                gr.update(), gr.update(), gr.update(),   # waiting, cur_stage, action_log
                gr.update(interactive=True),              # start_btn stays enabled
                gr.update(value="⚠ Fill in Jira Ticket, Branch, and Task first."),  # status
                gr.update(),                              # progress_bar
                gr.update(),                              # action_bar
            )

        # Clear stale queues
        while not output_q.empty(): output_q.get_nowait()
        while not input_q.empty():  input_q.get_nowait()
        if pipeline_thread and pipeline_thread.is_alive():
            input_q.put("__stop__")

        from pathlib import Path
        guidelines = ""
        claude_md = Path(repo) / "CLAUDE.md"
        if claude_md.exists():
            guidelines = claude_md.read_text()

        ctx = ProjectContext(
            repo_path=repo.strip(),
            language=lang,
            jira_ticket=ticket.strip(),
            task_description=task.strip(),
            branch_name=branch.strip(),
            test_commands=[l.strip() for l in tests_raw.splitlines() if l.strip()],
            lint_commands=[l.strip() for l in lint_raw.splitlines() if l.strip()],
            coding_guidelines=guidelines,
        )
        pipeline_thread = threading.Thread(
            target=run_pipeline_in_thread, args=(ctx,), daemon=True
        )
        pipeline_thread.start()

        return (
            [],                              # clear chatbot
            False,                           # reset waiting
            "plan",                          # reset stage
            "",                              # clear action_log state
            gr.update(interactive=False),    # disable start_btn
            gr.update(value="Running..."),
            progress_html("plan", True),
            gr.update(value="Starting..."),  # action_bar
        )

    def poll(history, is_waiting, stage, action_log):
        changed, new_waiting, new_stage = False, is_waiting, stage
        action_lines = action_log.splitlines() if action_log else []

        while not output_q.empty():
            kind, text = output_q.get_nowait()
            changed = True
            if kind == "stage":
                new_stage = text
                action_lines.append(f"--- {text.upper()} ---")
            elif kind == "status":
                action_lines.append(text)
                # Keep last 200 lines so it doesn't grow forever
                action_lines = action_lines[-200:]
            elif kind == "msg":
                history = history + [{"role": "assistant", "content": text}]
                new_waiting = False
            elif kind == "ask":
                history = history + [{"role": "assistant", "content": f"⏸  {text}"}]
                new_waiting = True
                action_lines.append("⏸ Waiting for your input...")
            elif kind == "done":
                history = history + [{"role": "assistant", "content": "✓ Pipeline finished."}]
                new_waiting = False
                new_stage = "done"
                action_lines.append("✓ Done")

        new_action_log = "\n".join(action_lines)
        spinning = not new_waiting and new_stage != "done"
        if not changed:
            return (
                history, new_waiting, new_stage, action_log,
                gr.update(interactive=new_waiting),
                gr.update(interactive=new_waiting),
                gr.update(), gr.update(), gr.update(),
            )
        return (
            history, new_waiting, new_stage, new_action_log,
            gr.update(interactive=new_waiting),
            gr.update(interactive=new_waiting),
            gr.update(value="Waiting for your input..." if new_waiting else "Running..."),
            gr.update(value=progress_html(new_stage, spinning)),
            gr.update(value=new_action_log),
        )

    def send_message(user_msg, history, is_waiting):
        if not user_msg.strip() or not is_waiting:
            return history, "", is_waiting
        history = history + [{"role": "user", "content": user_msg}]
        input_q.put(user_msg.strip())
        return history, "", False

    # ---- Wire events -------------------------------------------------------
    start_btn.click(
        start_pipeline,
        inputs=[fi_ticket, fi_branch, fi_lang, fi_task, fi_repo, fi_tests, fi_lint],
        outputs=[chatbot, waiting, cur_stage, action_log, start_btn, status, progress_bar, action_bar],
    )

    timer = gr.Timer(value=1.0)
    timer.tick(
        poll,
        inputs=[chatbot, waiting, cur_stage, action_log],
        outputs=[chatbot, waiting, cur_stage, action_log, msg_box, send_btn, status, progress_bar, action_bar],
    )

    send_btn.click(
        send_message,
        inputs=[msg_box, chatbot, waiting],
        outputs=[chatbot, msg_box, waiting],
    )
    msg_box.submit(
        send_message,
        inputs=[msg_box, chatbot, waiting],
        outputs=[chatbot, msg_box, waiting],
    )


print("[ui] gr.Blocks defined ok", flush=True)

WRAP_CSS = """
.message-wrap { white-space: pre-wrap !important; word-break: break-all !important; overflow-wrap: anywhere !important; }
.message-wrap p { white-space: pre-wrap !important; word-break: break-all !important; }
.chatbot { overflow-x: hidden !important; }
"""

if __name__ == "__main__":
    print("[ui] building Blocks UI...", flush=True)
    print(f"[ui] demo object: {demo}", flush=True)
    print("[ui] launching server on http://127.0.0.1:7860 ...", flush=True)
    demo.launch(share=False, css=WRAP_CSS)
