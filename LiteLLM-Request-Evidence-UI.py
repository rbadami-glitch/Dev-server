"""Windows desktop UI for one-request encrypted LiteLLM evidence export."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox, ttk

from litellm_request_evidence import (
    EvidenceError, check_operator_account, publish_request, remove_request, validate_request_id,
)


class EvidenceApp:
    def __init__(self) -> None:
        check_operator_account()
        self.root = tk.Tk()
        self.root.title("LiteLLM Request Evidence")
        self.root.geometry("690x320")
        self.root.resizable(False, False)
        self.request_id = tk.StringVar()
        self.status = tk.StringVar(value="Paste a Request ID from LiteLLM Logs.")
        self.link = tk.StringVar()
        self.busy = False

        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="LiteLLM Request Evidence",
                  font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Use the Request ID shown in LiteLLM Logs (for example chatcmpl-...). "
                 "A PCB-internal ID will not match.",
            wraplength=640,
        ).pack(anchor="w", pady=(8, 14))

        ttk.Label(frame, text="LiteLLM Request ID").pack(anchor="w")
        entry = ttk.Entry(frame, textvariable=self.request_id, width=90)
        entry.pack(fill="x", pady=(3, 11))
        entry.focus_set()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x")
        self.publish_button = ttk.Button(
            buttons, text="Collect and publish encrypted evidence",
            command=self.publish,
        )
        self.publish_button.pack(side="left")
        self.delete_button = ttk.Button(
            buttons, text="Delete published evidence", command=self.delete,
        )
        self.delete_button.pack(side="left", padx=(10, 0))
        self.copy_button = ttk.Button(buttons, text="Copy GitHub link",
                                      command=self.copy_link, state="disabled")
        self.copy_button.pack(side="left", padx=(10, 0))

        ttk.Separator(frame).pack(fill="x", pady=18)
        ttk.Label(frame, textvariable=self.status, wraplength=640).pack(anchor="w")
        ttk.Label(frame, textvariable=self.link, wraplength=640,
                  foreground="#155a9c").pack(anchor="w", pady=(7, 0))
        ttk.Label(
            frame,
            text="The public GitHub repository receives encrypted data only. "
                 "Delete it after analysis; encrypted copies remain in Git history.",
            wraplength=640,
        ).pack(anchor="w", pady=(18, 0))

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.publish_button.configure(state=state)
        self.delete_button.configure(state=state)

    def run_task(self, task, on_success) -> None:
        if self.busy:
            return
        self.set_busy(True)
        self.status.set("Working. No request contents or keys are shown here.")

        def worker() -> None:
            try:
                result = task()
            except EvidenceError as exc:
                message = str(exc)
                self.root.after(0, lambda: self.finish_error(message))
            except Exception:
                self.root.after(0, lambda: self.finish_error(
                    "Unexpected error. No request content was displayed."))
            else:
                self.root.after(0, lambda: on_success(result))

        threading.Thread(target=worker, daemon=True).start()

    def finish_error(self, message: str) -> None:
        self.set_busy(False)
        self.status.set(message)
        messagebox.showerror("LiteLLM evidence", message, parent=self.root)

    def publish(self) -> None:
        try:
            request_id = validate_request_id(self.request_id.get())
        except EvidenceError as exc:
            self.finish_error(str(exc))
            return

        def success(result: dict[str, str]) -> None:
            self.set_busy(False)
            self.link.set(result["url"])
            self.copy_button.configure(state="normal")
            self.status.set("Encrypted request record published. Commit: " + result["commit"][:12])
            messagebox.showinfo(
                "Published",
                "The encrypted record is on GitHub. Copy the link for analysis. "
                "When analysis is complete, return here and click Delete published evidence.",
                parent=self.root,
            )

        self.run_task(lambda: publish_request(request_id), success)

    def delete(self) -> None:
        try:
            request_id = validate_request_id(self.request_id.get())
        except EvidenceError as exc:
            self.finish_error(str(exc))
            return
        if not messagebox.askyesno(
            "Delete published evidence?",
            "Remove this encrypted request file from the current GitHub branch? "
            "Git history will still contain the encrypted version.",
            parent=self.root,
        ):
            return

        def success(result: dict[str, str]) -> None:
            self.set_busy(False)
            self.link.set("")
            self.copy_button.configure(state="disabled")
            self.status.set("Encrypted evidence removed from current branch. Commit: "
                            + result["commit"][:12])
            messagebox.showinfo(
                "Removed",
                "The encrypted file was removed from the current GitHub branch. "
                "Earlier encrypted copies remain in Git history.",
                parent=self.root,
            )

        self.run_task(lambda: remove_request(request_id), success)

    def copy_link(self) -> None:
        if self.link.get():
            self.root.clipboard_clear()
            self.root.clipboard_append(self.link.get())
            self.status.set("GitHub link copied.")

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    try:
        EvidenceApp().run()
    except EvidenceError as exc:
        raise SystemExit(str(exc)) from None
