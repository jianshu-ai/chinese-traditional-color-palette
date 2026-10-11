"""Model-authored dialogue events and deterministic next actions; no NLP or image API.

An event records the caller's interpretation of conversation evidence. It cannot
authenticate the user or inspect tools called outside this ledger.
"""
import copy


def initial(count=None, purpose="design"):
    return {"purpose": purpose, "stopped": False, "round_number": 0,
            "authorization": None, "request": None, "delivery": None,
            "history": [], "events": [], "preferences": [], "requested_count": count}


def labels(state):
    return list("ABCDEFGHIJ")[:state.get("target_count") or 0]


def validate_state(s, run, state):
    d = state["dialogue"]
    s.require(type(state["target_count"]) is int and state["target_count"] in (5, 10) or state["target_count"] is None,
              "invalid round count", "COUNT_INVALID")
    events = {}
    for item in d["events"]:
        event = item["event"]
        s.require(item["id"] == s.digest(event) and item["id"] not in events, "dialogue evidence changed", "BINDING_STALE")
        s.require(event["source"] == ("test_fixture" if state["run_mode"] == "test" else "conversation"), "event source mismatch", "CONFIRMATION_SOURCE")
        events[item["id"]] = item
    for pref in d["preferences"]:
        item = events.get(pref["event_id"], {})
        s.require({k: v for k, v in pref.items() if k not in {"event_id", "received_round"}} == item.get("feedback"),
                  "preference detached from event evidence", "BINDING_STALE")
    auth = d["authorization"]
    if auth:
        s.require(not d["stopped"] and d["purpose"] == "design", "stopped/analysis session has authorization", "BINDING_STALE")
        s.require(auth["round_number"] == d["round_number"] and auth["target_count"] == state["target_count"], "round binding changed", "BINDING_STALE")
        s.require(auth["event_ids"] and all(i in events for i in auth["event_ids"]) and
                  any(events[i]["event"]["action"] in {"start", "continue"} for i in auth["event_ids"]),
                  "round lacks structured user authorization evidence", "ROUND_AUTHORIZATION_REQUIRED")
    s.require(len(state["attempts"]) <= (state["target_count"] or 0) + state["retry_budget"], "round call budget exceeded", "BUDGET_EXHAUSTED")
    s.require(sum(a["kind"] == "retry" for a in state["attempts"]) <= state["retry_budget"], "round repair budget exceeded", "BUDGET_EXHAUSTED")
    for item in d["history"]:
        path = s.inside(run, item["state_path"])
        s.require(s.pc.sha(path.read_bytes()) == item["state_sha256"], "archived round changed", "BINDING_STALE")


def feedback(s, run, state, value):
    s.fields(value, ["likes", "dislikes", "focus", "no_new_feedback"], label="feedback")
    s.string_list(value["focus"], "modification focus")
    s.require(type(value["no_new_feedback"]) is bool, "no_new_feedback must be boolean")
    resolved = copy.deepcopy(value)
    for key in ("likes", "dislikes"):
        s.require(isinstance(value[key], list), "preferences must be arrays")
        for ref in resolved[key]:
            s.fields(ref, ["round_number", "solution_id", "reason"], label="preference reference")
            s.nonblank(ref["reason"], "what to retain or change")
            number = ref["round_number"]
            s.require(type(number) is int and number > 0, "reference needs an explicit round number")
            if number == state["dialogue"]["round_number"]:
                origin = state
            else:
                item = next((h for h in state["dialogue"]["history"] if h["round_number"] == number), None)
                s.require(item is not None, "unknown preference round", "REFERENCE_MISSING")
                path = s.inside(run, item["state_path"])
                s.require(s.pc.sha(path.read_bytes()) == item["state_sha256"], "archived round changed", "BINDING_STALE")
                origin = s.read_json(path)
            call = s.current_call(origin, ref["solution_id"])
            s.require(call is not None and call["status"] == "registered", "reference needs a recorded image", "REFERENCE_MISSING")
            path = s.inside(run, call["image_path"])
            s.require(s.pc.sha(path.read_bytes()) == call["image_sha256"], "preference image changed", "BINDING_STALE")
            ref.update(image_path=call["image_path"], image_sha256=call["image_sha256"], call_id=call["id"])
    has_feedback = any(value[k] for k in ("likes", "dislikes", "focus"))
    s.require(has_feedback != value["no_new_feedback"], "provide feedback or explicitly state no new feedback")
    return resolved


def apply_event(s, run, state, event):
    s.fields(event, ["action", "source", "user_reply", "message_reference"], ["target_count", "feedback"], "dialogue event")
    for key in ("user_reply", "message_reference"):
        s.nonblank(event[key], key)
    s.require(event["source"] == ("test_fixture" if state["run_mode"] == "test" else "conversation"),
              "test events cannot authorize live image tools", "CONFIRMATION_SOURCE")
    action = event["action"]
    s.require(action in {"start", "continue", "feedback", "stop"}, "unknown structured dialogue action")
    d = state["dialogue"]
    event_id = s.digest(event)
    if any(e["id"] == event_id for e in d["events"]):
        return  # Idempotent replay cannot revive a stopped authorization.
    s.require(not any(e["event"]["message_reference"] == event["message_reference"] and
                      e["event"]["action"] in {"start", "continue", "stop"} for e in d["events"]),
              "one conversation message cannot grant a second round", "AUTHORIZATION_REPLAY")
    count = event.get("target_count")
    s.require(count is None or type(count) is int and count in (5, 10), "choose 5 or 10", "COUNT_INVALID")
    s.require(action != "stop" or count is None and "feedback" not in event, "stop cannot carry future permission")
    item = {"id": event_id, "event": copy.deepcopy(event)}
    if "feedback" in event:
        item["feedback"] = feedback(s, run, state, event["feedback"])
    if action == "stop":
        d.update(stopped=True, request=None, authorization=None, requested_count=None)
    elif action in {"start", "continue"}:
        s.require(d["purpose"] == "design", "planning/diagnostic sessions cannot authorize images", "NON_GENERATING_TASK")
        s.require(action == ("start" if d["round_number"] == 0 else "continue"), "use start for first round, continue thereafter")
        s.require(d["round_number"] == 0 or d["delivery"] or d["stopped"],
                  "finish/deliver current round before requesting a new round", "ROUND_ACTIVE")
        d["stopped"] = False
        prior = d["request"] or {}
        d["request"] = {"event_ids": prior.get("event_ids", []) + [event_id],
                        "target_count": count if count is not None else prior.get("target_count", d["requested_count"]),
                        "feedback_id": event_id if "feedback" in item else prior.get("feedback_id")}
    elif action == "feedback":
        s.require("feedback" in item or count is not None, "record feedback or requested count")
        # Feedback/count alone never creates continuation permission.
        if d["request"]:
            if count is not None:
                d["request"]["target_count"] = count
            if "feedback" in item:
                d["request"]["feedback_id"] = event_id
            d["request"]["event_ids"].append(event_id)
        elif count is not None and not d["stopped"]:
            d["requested_count"] = count
    if "feedback" in item:
        d["preferences"].append({"event_id": event_id, "received_round": d["round_number"], **item["feedback"]})
        if d["request"] and not d["request"].get("feedback_id"):
            d["request"]["feedback_id"] = event_id
    # A prior preference on this delivered round can complete a later continue.
    if (d["request"] and not d["request"].get("feedback_id") and d["preferences"]
            and d["preferences"][-1]["received_round"] == d["round_number"]):
        d["request"]["feedback_id"] = d["preferences"][-1]["event_id"]
    d["events"].append(item)
    maybe_start(s, run, state)


def maybe_start(s, run, state):
    d, request = state["dialogue"], state["dialogue"]["request"]
    if (d["stopped"] or not request or s.brief_status(state) not in {"confirmed", "test_confirmed"}
            or request["target_count"] not in (5, 10)
            or d["round_number"] and not request["feedback_id"]
            or any(a["status"] == "pending" for a in state["attempts"])):
        return
    if d["round_number"]:
        # All old paths remain valid. Only the current working state is replaced.
        archive = run / "rounds" / f"round-{d['round_number']:03d}" / "state.json"
        s.require(not archive.exists(), "archived round already exists", "ROUND_EXISTS")
        s.pc.write_json(archive, state)
        d["history"].append({"round_number": d["round_number"], "state_path": str(archive),
                             "state_sha256": s.pc.sha(archive.read_bytes()), "delivery": copy.deepcopy(d["delivery"])})
    d["round_number"] += 1
    d["authorization"] = {**copy.deepcopy(request), "brief_binding": s.brief_binding(state),
                           "round_number": d["round_number"]}
    d.update(request=None, delivery=None, requested_count=None)
    state.update(target_count=request["target_count"], plan=None, attempts=[], reviews={})
    state.pop("plan_record_sha256", None)


def require_open(s, state):
    d = state["dialogue"]
    s.require(not d["stopped"], "session ended; no automatic generation permission remains", "SESSION_ENDED")
    s.require(d["authorization"] is not None and d["delivery"] is None, "a new round needs explicit authorization and 5/10 count", "ROUND_AUTHORIZATION_REQUIRED")
    s.require(d["authorization"]["brief_binding"] == s.brief_binding(state), "round authorization belongs to earlier goals", "ROUND_AUTHORIZATION_REQUIRED")


def binding(s, state):
    d = state["dialogue"]
    return {"round_number": d["round_number"], "authorization_sha256": s.digest(d["authorization"]),
            "preferences_sha256": s.digest(d["preferences"])}


def next_step(s, state):
    d = state["dialogue"]
    test = state["run_mode"] == "test"

    def out(phase, kind, action, **details):
        return {"phase": phase, "next_action": {"kind": kind, "action": action,
                "image_tool_allowed": False, **details}, "round_number": d["round_number"]}

    pending = [a["id"] for a in state["attempts"] if a["status"] == "pending"]
    if pending:
        return out("ended" if d["stopped"] else "recovering", "reconcile", "resolve_pending",
                   call_ids=pending, instruction="核对工具回执，register 或 call-failed；不得重发 pending 调用。")
    if d["stopped"]:
        return out("ended", "none", "stop")
    if d["purpose"] != "design":
        return out("non_generating", "none", "return_analysis")
    if s.brief_status(state) == "awaiting_proposal":
        return out("needs_requirements", "ask", "ask_requirements", question="希望重点改哪里、达到什么感觉？哪些内容必须保持？")
    if s.brief_status(state) == "awaiting_confirmation":
        return out("needs_requirements", "ask", "ask_missing_requirements", question="目标或保持项还有哪些需要补充或纠正？已有明确答复可直接登记。")
    if d["delivery"] and not d["request"]:
        return out("awaiting_continue", "ask", "ask_continue_or_end", question="这轮继续还是结束？继续请说喜欢、不喜欢或修改重点（可说无新意见），以及下一轮5张还是10张。")
    request = d["request"]
    if request and (request["target_count"] is None or d["round_number"] and not request["feedback_id"]):
        missing = []
        if request["target_count"] is None:
            missing.append("这一轮要5张还是10张？")
        if d["round_number"] and not request["feedback_id"]:
            missing.append("喜欢、不喜欢或想重点修改什么？也可以说无新意见。")
        return out("needs_round_details", "ask", "ask_round_details", question="".join(missing))
    if not d["authorization"] or d["authorization"]["brief_binding"] != s.brief_binding(state):
        return out("needs_round_authorization", "ask", "ask_count", question="这一轮做5张还是10张？")
    plan = state["plan"]
    if not plan or plan.get("round_binding") != binding(s, state) or plan.get("brief_binding") != s.brief_binding(state):
        return out("planning", "auto", "author_plan", instruction="自动更新本轮意图、配色与反馈应用，提交计划；无需再等开始生图。")
    for sid in labels(state):
        call = s.current_call(state, sid)
        if call and call["status"] == "registered":
            pointer = state["reviews"].get(sid)
            if not pointer or pointer["call_id"] != call["id"]:
                return out("reviewing", "auto", "view_and_review", solution_id=sid, image_path=call["image_path"])
            try:
                path = s.inside(s.Path(state["source_image"]["path"]).parent.parent, pointer["path"])
                s.require(s.pc.sha(path.read_bytes()) == pointer["sha256"], "review changed", "BINDING_STALE")
                review = s.read_json(path)
            except (s.pc.PaletteError, OSError):
                return out("recovering", "reconcile", "inspect_records", instruction="核对当前评审文件与绑定；未核实前不继续调用。")
            if review.get("failure_stage") == "diagnosis" and call["plan_sha256"] == s.plan_sha(state, sid):
                return out("planning", "auto", "revise_plan", solution_id=sid)
    for sid in labels(state):
        call = s.current_call(state, sid)
        if call is None:
            return out("generating", "auto", "fixture_call" if test else "reserve_and_call_image_tool",
                       solution_id=sid, image_tool_allowed=not test,
                       instruction="先 call 预登记；仅使用本次返回的原图和短提示。" if not test else "仅运行人工夹具，禁止调用真实图像服务。")
    retries = sum(a["kind"] == "retry" for a in state["attempts"])
    if retries < state["retry_budget"]:
        for sid in labels(state):
            call = s.current_call(state, sid)
            pointer = state["reviews"].get(sid)
            rejected = pointer and s.read_json(pointer["path"])["decision"] == "reject"
            if call["status"] == "failed" or call["plan_sha256"] != s.plan_sha(state, sid) or rejected:
                return out("repairing", "auto", "assess_repair_or_deliver", solution_id=sid,
                           instruction="有必要才定向修复，使用本轮共用剩余额度；也可如实交付partial。")
    return out("delivering", "auto", "export_and_deliver", instruction="导出、构建HTML并交付；主动询问继续还是结束，包括partial。")


def refresh(s, state):
    if state["schema_version"] == s.VERSION:
        state.update(next_step(s, state))


def public(state):
    d = state["dialogue"]
    return {"round_number": d["round_number"], "phase": state["phase"],
            "next_action": copy.deepcopy(state["next_action"]),
            "session_ended": d["stopped"], "purpose": d["purpose"],
            "preferences": copy.deepcopy(d["preferences"]), "round_history": copy.deepcopy(d["history"])}
