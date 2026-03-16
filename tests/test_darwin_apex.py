from sea_turtle.integrations.darwin_apex import last_iter_embeds


def test_last_iter_profit_push_uses_green_embeds() -> None:
    bundle = {
        "run": {
            "id": "42",
            "status": "completed",
            "trigger_type": "review",
            "started_at": "2026-03-16T00:00:00Z",
            "finished_at": "2026-03-16T00:10:00Z",
            "summary": "Completed a profit push iteration.",
            "decision_summary_json": "{}",
        },
        "artifact": {
            "focus_mode": {"mode": "profit_push", "reason": "push pnl"},
            "iteration_strategy": {"mode": "profit_push", "reason": "stay on revenue path"},
            "proposal": {"proposal_title": "Ship profit loop", "commit_message": "ship profit loop"},
            "gitops": {},
            "strategy_alignment": {},
            "diagnostics": {},
        },
        "latest_release": None,
        "running_run": None,
    }

    embeds = last_iter_embeds(bundle)

    assert embeds[0]["color"] == 0x2ECC71
    assert embeds[1]["color"] == 0x2ECC71
    assert embeds[2]["color"] == 0x27AE60
