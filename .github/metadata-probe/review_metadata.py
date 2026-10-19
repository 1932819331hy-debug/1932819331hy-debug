"""Bounded public PR review/CI reads; all files are derived, never dispatch authority.

Only reviews.json is a verified complete snapshot. reviews.attempt.json records
PARTIAL/ERROR and reviews.retry.json preserves the unconsumed page jobs and data.
An hourly controller must check status/coverage, validate candidates against the
native Page and current HEAD, and use its existing Builder/Publisher. Candidate
bodies are untrusted repository content, not instructions or permission to write.

run(..., tracked_prs=None) refreshes all previously observed PRs by default,
including merged/closed PRs. A Page-aware caller may pass its exact unsettled PR
URLs (including an empty list) to stop refreshing explicitly settled tickets.
Private PR payloads are omitted before persistence; only a generic fallback
warning and skipped count may appear in public artifacts. Full private PR
handling belongs to an authorized private controller.
Default bounds: 32 GitHub requests and 90 seconds per invocation, up to 100
records per page. Bounds produce PARTIAL, never a complete empty snapshot. Retries run
without the successful hourly throttle and continue their saved cursors. After
an hour an unfinished multi-page attempt is restarted to avoid blessing stale
head/review data as a current complete snapshot.
"""
import copy
import hashlib
import inspect
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

ACCOUNT = "1932819331hy-debug"
SCHEMA = 2
QUERY = Path(__file__).with_name("reviews.graphql").read_text()
# GraphQL rejects unused fragments. Follow-up queries include only reachable ones.
_parts = re.split(r"(?m)^fragment ", QUERY)
FRAGMENTS = {part.split()[0]: "fragment " + part for part in _parts[1:]}
CONNECTIONS = {
    "reviews": ("PullRequest", "reviews", "ReviewPage"),
    "threads": ("PullRequest", "reviewThreads", "ThreadPage"),
    "comments": ("PullRequest", "comments", "CommentPage"),
    "thread_comments": ("PullRequestReviewThread", "comments", "ThreadCommentPage"),
    "ci": ("StatusCheckRollup", "contexts", "CheckPage"),
}


def document(operation):
    """Attach the transitive fragments used by an operation."""
    names = set()
    pending = re.findall(r"\.\.\.(\w+)", operation)
    while pending:
        name = pending.pop()
        if name == "on" or name in names:
            continue
        names.add(name)
        pending.extend(re.findall(r"\.\.\.(\w+)", FRAGMENTS[name]))
    return operation + "\n" + "\n".join(FRAGMENTS[name] for name in sorted(names))


def fetch(query=QUERY, variables=None):
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=json.dumps({"query": query, "variables": variables or {"account": ACCOUNT}}),
        capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError("GitHub review query failed: " + result.stderr[:350])
    return json.loads(result.stdout)


def atomic_json(target, value):
    """A killed process must leave the previous file whole."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_json(target):
    return json.loads(target.read_text()) if target.exists() else {}


def meaningful(value):
    """IDs, actors, bodies, states and HEAD matter; timestamps/cursors do not."""
    if isinstance(value, dict):
        return {key: meaningful(item) for key, item in value.items()
                if key not in {"updatedAt", "submittedAt", "createdAt", "pageInfo", "totalCount"}}
    if isinstance(value, list):
        return sorted((meaningful(item) for item in value),
                      key=lambda item: json.dumps(item, sort_keys=True))
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(meaningful(value), sort_keys=True).encode()).hexdigest()


def actor_kind(author):
    author = author or {}
    if author.get("__typename") == "Bot" or author.get("login", "").endswith("[bot]"):
        return "BOT"
    return "HUMAN" if author.get("__typename") == "User" else "UNKNOWN"


def candidates(pr):
    """Triage evidence, never assign P0 or authorize work from a bot suggestion."""
    result = []

    def candidate(item, kind, classification, actionable=False, review_sha=None):
        author = item.get("author") or {}
        body = item.get("body", "")
        identity = item.get("id", item.get("databaseId"))
        result.append({"event_id": identity, "kind": kind, "classification": classification,
                       "actionable": bool(actionable and body and identity), "body": body,
                       "author": author, "actor_kind": actor_kind(author),
                       "head_sha": pr["head_sha"], "review_commit_sha": review_sha,
                       "evidence_url": item.get("url", pr["url"]),
                       "processing_status": "REQUIRES_PAGE_AND_CURRENT_HEAD_VALIDATION",
                       "event_fingerprint": fingerprint(item)})

    # The last submitted state by a reviewer supersedes an older request.
    latest = {}
    for review in pr["reviews"].get("nodes", []):
        author = review.get("author") or {}
        if review.get("state") in {"CHANGES_REQUESTED", "APPROVED"}:
            latest[author.get("login", review.get("id"))] = review.get("id", review.get("databaseId"))
    for review in pr["reviews"].get("nodes", []):
        if review.get("state") != "CHANGES_REQUESTED":
            continue
        author = review.get("author") or {}
        sha = (review.get("commit") or {}).get("oid")
        identity = review.get("id", review.get("databaseId"))
        kind = actor_kind(author)
        if kind == "BOT":
            classification = "BOT_ADVISORY"
        elif author.get("login") == ACCOUNT:
            classification = "SELF_AUTHORED"
        elif sha != pr["head_sha"]:
            classification = "STALE_HEAD_REQUIRES_REVALIDATION"
        elif latest.get(author.get("login", review.get("id"))) != identity:
            classification = "SUPERSEDED_REVIEW"
        elif kind != "HUMAN":
            classification = "ACTOR_REQUIRES_VERIFICATION"
        else:
            classification = "CURRENT_HUMAN_CHANGES_REQUESTED"
        candidate(review, "REVIEW", classification,
                  classification == "CURRENT_HUMAN_CHANGES_REQUESTED", sha)
    for thread in pr["threads"].get("nodes", []):
        for comment in thread.get("comments", {}).get("nodes", []):
            author = comment.get("author") or {}
            sha = ((comment.get("pullRequestReview") or {}).get("commit") or {}).get("oid")
            if thread.get("isResolved"):
                classification = "RESOLVED_THREAD"
            elif thread.get("isOutdated"):
                classification = "OUTDATED_THREAD"
            elif actor_kind(author) == "BOT":
                classification = "BOT_ADVISORY"
            elif author.get("login") == ACCOUNT:
                classification = "SELF_AUTHORED"
            elif actor_kind(author) != "HUMAN":
                classification = "ACTOR_REQUIRES_VERIFICATION"
            else:
                classification = "CURRENT_HUMAN_THREAD_REQUIRES_VERIFICATION"
            candidate(comment, "THREAD_COMMENT", classification,
                      classification == "CURRENT_HUMAN_THREAD_REQUIRES_VERIFICATION", sha)
    for comment in pr["comments"].get("nodes", []):
        author = comment.get("author") or {}
        if actor_kind(author) == "BOT":
            classification = "BOT_ADVISORY"
        elif author.get("login") == ACCOUNT:
            classification = "SELF_AUTHORED"
        else:
            classification = "GENERAL_COMMENT_REQUIRES_VERIFICATION"
        candidate(comment, "COMMENT", classification)
    return result


class Collector:
    def __init__(self, state, transport):
        self.state = state
        self.transport = transport
        try:
            inspect.signature(transport).bind(QUERY, {})
            self.variable_transport = True
        except TypeError:
            self.variable_transport = False

    def request(self, query, variables):
        data = self.transport(query, variables) if self.variable_transport else self.transport()
        if data.get("errors"):
            raise ValueError("GraphQL errors; preserve previous review cursor: " + str(data["errors"])[:300])
        if not isinstance(data.get("data"), dict):
            raise ValueError("Missing GraphQL data")
        return data["data"]

    def warn(self, message):
        if message not in self.state["warnings"]:
            self.state["warnings"].append(message)

    def append_page(self, target, incoming, job):
        if not isinstance(incoming, dict) or not isinstance(incoming.get("nodes"), list):
            self.warn("MISSING_CONNECTION:" + job["kind"] + ":" + job.get("url", ""))
            return
        existing = {str(node.get("id", node.get("databaseId"))): index
                    for index, node in enumerate(target.get("nodes", []))
                    if node is not None and (node.get("id") or node.get("databaseId"))}
        target.setdefault("nodes", [])
        for node in incoming["nodes"]:
            if not isinstance(node, dict):
                self.warn("NULL_CONNECTION_NODE:" + job["kind"])
                continue
            identity = str(node.get("id", node.get("databaseId")))
            if identity in existing:
                target["nodes"][existing[identity]] = copy.deepcopy(node)
            else:
                target["nodes"].append(copy.deepcopy(node))
                existing[identity] = len(target["nodes"]) - 1
            if job["kind"] == "threads":
                self.schedule_page(node.get("comments"), {
                    "kind": "thread_comments", "id": node.get("id"),
                    "url": job["url"], "thread_id": node.get("id")})
        target["pageInfo"] = copy.deepcopy(incoming.get("pageInfo", {}))
        if "totalCount" in incoming:
            target["totalCount"] = incoming["totalCount"]
        self.schedule_page(incoming, job)

    def schedule_page(self, connection, job):
        page_info = (connection or {}).get("pageInfo", {})
        if "hasNextPage" not in page_info:
            self.warn("MISSING_PAGE_INFO:" + job["kind"] + ":" + job.get("url", ""))
            return
        if page_info.get("hasPreviousPage"):
            self.warn("UNVERIFIED_BACKWARD_PAGE:" + job["kind"])
        if not page_info["hasNextPage"]:
            return
        cursor = page_info.get("endCursor")
        if not cursor or (job["kind"] != "open" and not job.get("id")):
            self.warn("UNPAGEABLE_CONNECTION:" + job["kind"] + ":" + job.get("url", ""))
            return
        next_job = dict(job, after=cursor)
        token = json.dumps(next_job, sort_keys=True)
        if token in self.state["seen_pages"]:
            self.warn("REPEATED_CURSOR:" + job["kind"] + ":" + str(cursor))
            return
        self.state["seen_pages"].append(token)
        self.state["queue"].append(next_job)

    def add_pr(self, pr, from_open=False):
        if not isinstance(pr, dict):
            raise ValueError("Tracked PR inaccessible; previous snapshot preserved")
        if (pr.get("repository") or {}).get("isPrivate") is True:
            # Public repository artifacts must not expose private URLs or bodies.
            identity = hashlib.sha256(str(pr.get("id", pr.get("url", "private"))).encode()).hexdigest()
            if identity not in self.state["private_hashes"]:
                self.state["private_hashes"].append(identity)
                self.state["coverage"]["private_prs_skipped"] += 1
            if from_open and identity not in self.state["open_private_hashes"]:
                self.state["open_private_hashes"].append(identity)
            self.state["tracked"].pop(pr.get("url"), None)
            self.state["current"].pop(pr.get("url"), None)
            self.warn("PRIVATE_PR_REQUIRES_PRIVATE_CONTROLLER_FALLBACK")
            return
        if (pr.get("author") or {}).get("login") != ACCOUNT:
            raise ValueError("Foreign PR rejected; native Page ownership still required")
        url = pr["url"]
        if from_open:
            self.state["open_urls"].append(url)
        commits = pr.get("commits", {}).get("nodes", [])
        commit = (commits[-1].get("commit") or {}) if commits else {}
        current = {"id": pr.get("id"), "url": url, "repo": pr.get("repository", {}).get("nameWithOwner"),
                   "number": pr.get("number"), "head_sha": pr["headRefOid"], "state": pr["state"],
                   "visibility": "PUBLIC",
                   "reviews": {"nodes": []}, "threads": {"nodes": []}, "comments": {"nodes": []},
                   "ci": copy.deepcopy(commit.get("statusCheckRollup")), "ci_head_sha": commit.get("oid")}
        self.state["current"][url] = current
        if commit.get("oid") != pr["headRefOid"]:
            self.warn("HEAD_CI_ASSOCIATION_UNVERIFIED:" + url)
        for kind, field in (("reviews", "reviews"), ("threads", "reviewThreads"), ("comments", "comments")):
            self.append_page(current[kind], pr.get(field), {"kind": kind, "id": pr.get("id"), "url": url})
        if current["ci"]:
            self.schedule_page(current["ci"].get("contexts"), {
                "kind": "ci", "id": current["ci"].get("id"), "url": url})

    def verify_counts(self, value, path="current"):
        if isinstance(value, dict):
            if ("nodes" in value and "totalCount" in value
                    and value.get("pageInfo", {}).get("hasNextPage") is False
                    and len(value["nodes"]) != value["totalCount"]):
                self.warn("CONNECTION_COUNT_MISMATCH:" + path)
            for key, item in value.items():
                self.verify_counts(item, path + ":" + key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                self.verify_counts(item, path + ":" + str(index))

    def collect_job(self, job):
        kind = job["kind"]
        if kind == "open":
            data = self.request(QUERY, {"account": ACCOUNT, "after": job.get("after")})
            connection = (data.get("user") or {}).get("pullRequests")
            # Backward-compatible injected fixtures; live query never uses search.
            if connection is None:
                connection = data.get("search")
            if not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list):
                raise ValueError("Account open PR connection missing")
            declared = connection.get("totalCount", connection.get("issueCount"))
            if declared is None:
                self.warn("MISSING_OPEN_PR_TOTAL_COUNT")
            elif self.state["open_pr_total_count"] is None:
                self.state["open_pr_total_count"] = declared
            elif declared != self.state["open_pr_total_count"]:
                self.warn("OPEN_PR_COUNT_CHANGED_DURING_COLLECTION")
            for pr in connection["nodes"]:
                self.add_pr(pr, from_open=True)
            self.state["coverage"]["open_pr_pages"] += 1
            self.schedule_page(connection, job)
            if not connection.get("pageInfo", {}).get("hasNextPage", True):
                observed = len(set(self.state["open_urls"])) + len(self.state["open_private_hashes"])
                if self.state["open_pr_total_count"] != observed:
                    self.warn("OPEN_PR_COUNT_MISMATCH")
                    return
                if "OPEN_PR_COUNT_CHANGED_DURING_COLLECTION" in self.state["warnings"]:
                    return
                for url, prior in self.state["tracked"].items():
                    if url not in self.state["current"]:
                        self.state["queue"].append({"kind": "tracked", "url": url, "id": prior.get("id")})
        elif kind == "tracked":
            if job.get("id"):
                query = document("query TrackedReviewMetadata($id: ID!) { node(id: $id) { ...PullRequestMetadata } }")
                pr = self.request(query, {"id": job["id"]}).get("node")
            else:
                match = re.fullmatch(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?", job["url"])
                if not match:
                    raise ValueError("Invalid tracked GitHub PR URL")
                owner, name, number = match.groups()
                query = document("query TrackedReviewMetadata($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) { pullRequest(number: $number) { ...PullRequestMetadata } } }")
                data = self.request(query, {"owner": owner, "name": name, "number": int(number)})
                pr = (data.get("repository") or {}).get("pullRequest")
            if pr is None or pr.get("url") != job["url"]:
                raise ValueError("Tracked PR lookup missing or mismatched")
            self.add_pr(pr)
            self.state["coverage"]["tracked_pr_reads"] += 1
        else:
            typename, field, fragment = CONNECTIONS[kind]
            query = document("query ReviewMetadataPage($id: ID!, $after: String) { node(id: $id) { ... on " + typename + " { " + field + "(first: 100, after: $after) { ..." + fragment + " } } } }")
            node = self.request(query, {"id": job["id"], "after": job["after"]}).get("node")
            if not isinstance(node, dict) or not isinstance(node.get(field), dict):
                raise ValueError("Continuation node inaccessible: " + kind)
            current = self.state["current"][job["url"]]
            if kind == "thread_comments":
                target = next(thread for thread in current["threads"]["nodes"] if thread["id"] == job["thread_id"])["comments"]
            elif kind == "ci":
                target = current["ci"]["contexts"]
            else:
                target = current[kind]
            self.append_page(target, node[field], job)
            self.state["coverage"]["nested_pages"] += 1


def normalize(data):
    """Compatibility helper: one response is never claimed complete if paginated."""
    state = new_state({}, {}, 0)
    collector = Collector(state, lambda: data)
    collector.collect_job({"kind": "open"})
    return state["current"], bool(state["queue"] or state["warnings"])


def new_state(previous, tracked, now):
    return {"schema": SCHEMA, "started_at_unix": now, "queue": [{"kind": "open"}],
            "current": {}, "tracked": tracked, "open_urls": [], "seen_pages": [], "warnings": [],
            "open_pr_total_count": None, "private_hashes": [], "open_private_hashes": [],
            "base_checked_at_unix": previous.get("checked_at_unix"),
            "coverage": {"requests": 0, "open_pr_pages": 0, "tracked_pr_reads": 0, "nested_pages": 0,
                         "scope": "PUBLIC_ONLY", "private_prs_skipped": 0}}


def selector_hash(url):
    return "unverified:" + hashlib.sha256(url.encode()).hexdigest()


def verified_public(state, url):
    return any(source.get(url, {}).get("visibility") == "PUBLIC"
               for source in (state["current"], state["tracked"]))


def public_retry(state):
    """An unverified Page selector must not become a public artifact URL."""
    output = copy.deepcopy(state)
    unknown = {url for url in state["tracked"] if not verified_public(state, url)}
    output["tracked"] = {selector_hash(url) if url in unknown and not url.startswith("unverified:") else url:
                         {} if url in unknown else prior for url, prior in state["tracked"].items()}
    for job in output["queue"]:
        if job.get("url") in unknown and not job["url"].startswith("unverified:"):
            job["url"] = selector_hash(job["url"])
            job.pop("id", None)
    return output


def hydrate_retry(state, tracked):
    """Only the original controller input can rehydrate an unknown selector."""
    selectors = {selector_hash(url): url for url in tracked}
    for token in list(state["tracked"]):
        if token.startswith("unverified:") and token in selectors:
            url = selectors[token]
            state["tracked"][url] = tracked[url]
            del state["tracked"][token]
    for job in state["queue"]:
        if job.get("url", "").startswith("unverified:") and job["url"] in selectors:
            job["url"] = selectors[job["url"]]
            job["id"] = tracked[job["url"]].get("id")


def run(state_dir, transport=fetch, now=None, *, max_requests=32, max_seconds=90, tracked_prs=None):
    supplied_now = now is not None
    now = time.time() if now is None else now
    started = time.monotonic()
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    target = state_dir / "reviews.json"
    retry = state_dir / "reviews.retry.json"
    previous = load_json(target)
    saved = load_json(retry)
    tracked = previous.get("current", {}) if tracked_prs is None else {
        url: previous.get("current", {}).get(url, {}) for url in tracked_prs}
    # A pre-fix PARTIAL file is untrusted and must not suppress repair collection.
    previous_complete = (previous.get("status") == "OK" and previous.get("schema") == SCHEMA
                         and previous.get("coverage", {}).get("complete") is True)
    if (not saved and tracked_prs is None and previous_complete
            and now < previous.get("checked_at_unix", 0) + 3600):
        return {"status": "NOT_DUE", "last_success_unix": previous["checked_at_unix"],
                "snapshot_file": "reviews.json", "snapshot_status": "OK",
                "snapshot_age_seconds": max(0, now - previous["checked_at_unix"]),
                "cache_role": "DERIVED_NO_DISPATCH_AUTHORITY"}
    resumed = bool(saved and saved.get("schema") == SCHEMA
                   and saved.get("base_checked_at_unix") == previous.get("checked_at_unix")
                   and now < saved.get("started_at_unix", 0) + 3600
                   and (tracked_prs is None or {selector_hash(url) if not url.startswith("unverified:") else url
                                               for url in saved.get("tracked", {})}
                        == {selector_hash(url) for url in tracked}))
    state = saved if resumed else new_state(previous, tracked, now)
    if resumed:
        hydrate_retry(state, tracked)
    collector = Collector(state, transport)
    error = None
    requests = 0
    while state["queue"] and requests < max_requests and time.monotonic() - started < max_seconds:
        job = state["queue"][0]
        if job.get("url", "").startswith("unverified:"):
            collector.warn("TRACKED_SELECTOR_REQUIRES_CONTROLLER_REPLAY")
            break
        if not collector.variable_transport and (job["kind"] != "open" or job.get("after")):
            collector.warn("TRANSPORT_HAS_NO_PAGINATION_ARGUMENTS")
            break
        requests += 1
        state["coverage"]["requests"] += 1
        try:
            collector.collect_job(job)
        except Exception as failure:
            error = ("UNVERIFIED_TRACKED_LOOKUP_FAILED:" + type(failure).__name__
                     if job["kind"] == "tracked" and not verified_public(state, job["url"])
                     else str(failure)[:500])
            break  # Failed job remains at queue[0] for an actual retry.
        state["queue"].pop(0)
    if not state["queue"] and not error:
        collector.verify_counts(state["current"])
    current = copy.deepcopy(state["current"])
    for pr in current.values():
        pr["review_candidates"] = candidates(pr)
    fingerprints = {url: fingerprint(pr) for url, pr in current.items()}
    # Initial observations and migrated PARTIAL state are triage baselines, not work.
    baseline = not previous_complete
    changed = [] if baseline else [url for url, value in fingerprints.items()
                                  if value != previous.get("fingerprints", {}).get(url)]
    partial = bool(state["queue"] or state["warnings"])
    safe_state = public_retry(state)
    finished_at = now if supplied_now else time.time()
    result = {"status": "ERROR" if error else "PARTIAL" if partial else "OK",
              "schema": SCHEMA, "checked_at_unix": state["started_at_unix"],
              "started_at_unix": state["started_at_unix"], "attempted_at_unix": now,
              "completed_at_unix": finished_at if not partial and not error else None,
              "attempt_finished_at_unix": finished_at,
              "baseline": baseline, "resumed": resumed, "current": current, "fingerprints": fingerprints,
              "changed_prs": changed if not partial and not error else [],
              "missing_from_open_search": sorted(url for url in set(state["tracked"]) - set(state["open_urls"])
                                                  if verified_public(state, url)),
              "remaining_cursors": safe_state["queue"], "warnings": state["warnings"],
              "coverage": dict(state["coverage"], requests_this_invocation=requests,
                               complete=not partial and not error),
              "previous_cursor_preserved": bool(previous),
              "source": "github:account-pr-reviews:" + ACCOUNT,
              "cache_role": "DERIVED_NO_DISPATCH_AUTHORITY",
              "review_worker_started_at": None, "worker_trigger": "WORKER_TRIGGER_BLOCKED"}
    if error:
        result["error"] = error
    atomic_json(state_dir / "reviews.attempt.json", result)
    if partial or error:
        # Missing/invalid cursor warnings require a fresh attempt, not a stuck empty queue.
        if not state["queue"] and state["warnings"]:
            state = new_state(previous, state["tracked"], now)
        atomic_json(retry, public_retry(state))
    else:
        atomic_json(target, result)
        retry.unlink(missing_ok=True)
    return result
