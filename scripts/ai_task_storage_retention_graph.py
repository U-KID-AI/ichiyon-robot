"""Pure, read-only retention graph analysis (Python 3.8 standard library).

The input is an already collected, sanitized inventory.  This module deliberately
has no filesystem, process, Docker, or network interface.  A DELETE_CANDIDATE is a
member of one joint proposal, never permission to remove a single object without
re-reading its references.  Policy age/count limits only select additional keeps.
"""

import copy
import math

from ai_task_storage_cleanup import build_cleanup_plan


CLASSIFICATIONS = (
    "KEEP_REQUIRED", "KEEP_POLICY", "DELETE_CANDIDATE", "NEEDS_REVIEW", "ACTIVE"
)
CATEGORIES = ("releases", "backups", "images", "staging")
DEFAULT_POLICY = {"backup_latest": 3, "backup_daily_days": 7, "release_latest": 3}
_FIELDS = (
    "id", "path", "sha", "allocated_bytes", "reclaimable_bytes", "size_complete",
    "reclaimable_complete", "validation", "created_at", "managed", "references_complete",
    "unknown_reference_kinds", "image_id", "immutable_image", "rollback_images",
    "required_images", "required_releases", "target_release", "previous_release",
    "size_bytes", "tags", "layers", "layers_complete", "active", "possible_active",
    "operation_id", "owner_state", "lock_state", "ready", "checksum_verified",
    "evidence_required", "ownership_verified", "validation_errors", "kind",
    "release_refs", "image_refs", "backup_refs", "metadata_errors", "metadata_files",
    "entries", "timestamp_source", "ctime", "checksums", "restore_validation",
    "ownership", "operation_sha", "checksum_manifest_present", "lock_held_during_observation",
    "layer_sizes_complete",
    "backup_format_version", "recovery_archive_ids",
)


def _amount(value):
    """Unknown and invalid sizes must not silently become measured zero."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _strings(value):
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _policy(value):
    result = dict(DEFAULT_POLICY)
    if value is not None:
        if not isinstance(value, dict) or set(value) - set(result):
            raise ValueError("unsupported retention policy")
        for name, count in value.items():
            if _amount(count) is None:
                raise ValueError("retention policy counts must be nonnegative integers")
            result[name] = count
    return result


def build_plan(snapshot, policy=None):
    """Classify one inventory without mutating it or accessing external state.

    Nodes require explicit ``validation='verified'``, complete size/reference
    observations, and management ownership to become candidates.  Unreadable
    references can be scoped with ``unknown_reference_kinds`` on any node or the
    snapshot.  A globally incomplete inventory disables every candidate.
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    policy = _policy(policy)
    nodes = {category: {} for category in CATEGORIES}
    warnings = []
    references = []
    reference_keys = set()
    unknown = set(_strings(snapshot.get("unknown_reference_kinds"))) & set(CATEGORIES)
    globally_complete = snapshot.get("references_complete") is True
    if not globally_complete:
        unknown.update(CATEGORIES)
        warnings.append("INCOMPLETE_REFERENCE_INVENTORY")

    for category in CATEGORIES:
        inventory = snapshot.get(category)
        if not isinstance(inventory, list):
            unknown.add(category)
            if category in ("releases", "backups", "staging"):
                unknown.update(("releases", "images"))
            warnings.append("INVALID_" + category.upper() + "_INVENTORY")
            continue
        for index, raw in enumerate(inventory):
            if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
                unknown.add(category)
                if category in ("releases", "backups", "staging"):
                    unknown.update(("releases", "images"))
                warnings.append("INVALID_" + category.upper() + "_NODE")
                continue
            item = {key: copy.deepcopy(raw[key]) for key in _FIELDS if key in raw}
            item["classification"] = "DELETE_CANDIDATE"
            item["reasons"] = []
            item["retained_by"] = []
            if item["id"] in nodes[category]:
                existing = nodes[category][item["id"]]
                # Docker tags can produce repeat rows. Identical identities and
                # facts count once; contradictory inventory is not a safe plan.
                comparison = {k: v for k, v in existing.items() if k not in ("classification", "reasons", "retained_by")}
                if comparison != {k: v for k, v in item.items() if k not in ("classification", "reasons", "retained_by")}:
                    existing["validation"] = "invalid"
                    existing["reasons"].append("CONFLICTING_DUPLICATE_IDENTITY")
                    unknown.add(category)
                    if category in ("releases", "backups", "staging"):
                        unknown.update(("releases", "images"))
                continue
            nodes[category][item["id"]] = item
            unknown.update(set(_strings(raw.get("unknown_reference_kinds"))) & set(CATEGORIES))
            if raw.get("references_complete") is not True:
                if category in ("releases", "backups"):
                    unknown.update(("releases", "images"))

    aliases = {}
    ambiguous_images = set()
    for ident, item in nodes["images"].items():
        for alias in [ident] + _strings(item.get("tags")):
            if alias in aliases and aliases[alias] != ident:
                ambiguous_images.add(alias)
            else:
                aliases[alias] = ident

    def reason(item, message):
        if message not in item["reasons"]:
            item["reasons"].append(message)

    def review(item, message):
        if item["classification"] != "ACTIVE":
            item["classification"] = "NEEDS_REVIEW"
        reason(item, message)

    def unresolved(category):
        unknown.add(category)
        if category in ("releases", "backups"):
            unknown.update(("releases", "images"))

    def protect(category, ident, classification, message, source=None):
        if not isinstance(ident, str) or not ident:
            unresolved(category)
            return False
        if category == "images":
            if ident in ambiguous_images:
                unknown.add("images")
                warnings.append("AMBIGUOUS_IMAGE_REFERENCE")
                return False
            ident = aliases.get(ident, ident)
        key = (source or "inventory", category + ":" + ident, message)
        if key not in reference_keys:
            reference_keys.add(key)
            references.append({"from": key[0], "to": key[1], "reason": message})
        item = nodes[category].get(ident)
        if item is None:
            warnings.append("MISSING_REFERENCED_" + category.upper() + ":" + ident)
            unresolved(category)
            return False
        if source and source not in item["retained_by"]:
            item["retained_by"].append(source)
        rank = {"DELETE_CANDIDATE": 0, "KEEP_POLICY": 1, "KEEP_REQUIRED": 2,
                "NEEDS_REVIEW": 3, "ACTIVE": 4}
        if rank[classification] > rank[item["classification"]]:
            item["classification"] = classification
        reason(item, message)
        return True

    # Validation uncertainty is independent of whether a node also has hard pins.
    for category in CATEGORIES:
        for item in nodes[category].values():
            if item.get("active") is True or item.get("possible_active") is True or item.get("ownership") == "possible_active":
                item["classification"] = "ACTIVE"
                reason(item, "OPERATION_ACTIVE_OR_POSSIBLY_ACTIVE")
            if category == "staging":
                if item["classification"] != "ACTIVE":
                    review(item, "STAGING_REQUIRES_OPERATION_OWNERSHIP_AND_EVIDENCE_REVIEW")
                continue
            if item.get("validation") != "verified":
                review(item, "METADATA_NOT_VERIFIED")
            if item.get("managed") is not True:
                review(item, "MANAGEMENT_OWNERSHIP_NOT_VERIFIED")
            if item.get("size_complete") is not True or _amount(item.get("size_bytes") if category == "images" else item.get("allocated_bytes")) is None:
                review(item, "SIZE_MEASUREMENT_INCOMPLETE")
            if item.get("references_complete") is not True:
                review(item, "NODE_REFERENCE_INVENTORY_INCOMPLETE")
            for field, dependency_kind in (("required_releases", "releases"),
                                           ("required_images", "images"),
                                           ("rollback_images", "images"),
                                           ("release_refs", "releases"),
                                           ("image_refs", "images"),
                                           ("backup_refs", "backups")):
                if field in item and (not isinstance(item[field], list) or
                                      len(_strings(item[field])) != len(item[field])):
                    review(item, "MALFORMED_REFERENCE_LIST")
                    unknown.add(dependency_kind)
            if category == "releases":
                image_ref = item.get("image_id") or item.get("immutable_image")
                if not isinstance(image_ref, str) or not image_ref:
                    review(item, "RELEASE_IMAGE_REFERENCE_UNAVAILABLE")
                    unknown.add("images")
                elif aliases.get(image_ref, image_ref) not in nodes["images"]:
                    review(item, "RELEASE_IMAGE_UNAVAILABLE")
            if category == "backups":
                previous_ref = item.get("previous_release")
                if not isinstance(previous_ref, str) or not previous_ref:
                    review(item, "RESTORE_RELEASE_REFERENCE_UNAVAILABLE")
                    unknown.update(("releases", "images"))
                elif previous_ref not in nodes["releases"]:
                    review(item, "RESTORE_RELEASE_UNAVAILABLE")
                if not isinstance(item.get("target_release"), str) or not item["target_release"]:
                    review(item, "BACKUP_TARGET_RELEASE_UNAVAILABLE")
                elif item["target_release"] not in nodes["releases"]:
                    review(item, "BACKUP_TARGET_RELEASE_UNAVAILABLE")
            timestamp_needed = ((category == "releases" and policy["release_latest"]) or
                                (category == "backups" and (policy["backup_latest"] or policy["backup_daily_days"])))
            if timestamp_needed and _timestamp(item.get("created_at")) is None:
                review(item, "RETENTION_POLICY_TIMESTAMP_UNAVAILABLE")

    current = snapshot.get("current_release")
    previous = snapshot.get("previous_known_good")
    if not isinstance(current, str) or current not in nodes["releases"]:
        unknown.update(("releases", "backups", "images"))
        warnings.append("CURRENT_RELEASE_UNAVAILABLE")
    if not isinstance(previous, str) or previous not in nodes["releases"]:
        unresolved("releases")
        warnings.append("PREVIOUS_KNOWN_GOOD_RELEASE_UNAVAILABLE")
    protect("releases", current, "KEEP_REQUIRED", "CURRENT_RELEASE", "current")
    protect("releases", previous, "KEEP_POLICY", "PREVIOUS_KNOWN_GOOD_RELEASE", "previous-known-good")
    for ident in _strings(snapshot.get("runtime_releases")):
        protect("releases", ident, "KEEP_REQUIRED", "RUNTIME_RELEASE", "runtime")

    pins = snapshot.get("explicit_pins", {})
    if not isinstance(pins, dict):
        unknown.update(CATEGORIES)
        warnings.append("INVALID_EXPLICIT_PIN_INVENTORY")
        pins = {}
    for category in ("releases", "backups", "images"):
        if category in pins and (not isinstance(pins[category], list) or
                                 len(_strings(pins[category])) != len(pins[category])):
            unresolved(category)
            warnings.append("INVALID_EXPLICIT_" + category.upper() + "_PINS")
        for ident in _strings(pins.get(category)):
            protect(category, ident, "KEEP_REQUIRED", "EXPLICIT_PIN", "explicit-pin")

    containers = snapshot.get("containers")
    if not isinstance(containers, list):
        unknown.update(("images", "releases"))
        warnings.append("INVALID_CONTAINER_INVENTORY")
        containers = []
    for container in containers:
        if not isinstance(container, dict):
            unknown.update(("images", "releases"))
            continue
        source = "container:" + str(container.get("id", "unknown"))
        message = "RUNNING_CONTAINER_IMAGE" if container.get("running") is True else "STOPPED_CONTAINER_IMAGE"
        if not protect("images", container.get("image_id"), "KEEP_REQUIRED", message, source):
            unknown.add("images")
        if container.get("release_sha"):
            protect("releases", container["release_sha"], "KEEP_REQUIRED", "CONTAINER_RELEASE", source)

    # Literal rollback pins survive even a proposed retirement of their owning
    # release. P1b must explicitly retire those pins in a separately reviewed step.
    for item in nodes["releases"].values():
        for ident in _strings(item.get("rollback_images")):
            protect("images", ident, "KEEP_REQUIRED", "LITERAL_ROLLBACK_IMAGE_PIN", "releases:" + item["id"])

    def newest(category):
        valid = [item for item in nodes[category].values()
                 if item.get("validation") == "verified" and _timestamp(item.get("created_at")) is not None]
        return sorted(valid, key=lambda item: (item["created_at"], item["id"]), reverse=True)

    for item in newest("releases")[:policy["release_latest"]]:
        protect("releases", item["id"], "KEEP_POLICY", "ADDITIONAL_LATEST_RELEASE_POLICY", "proposed-policy")
    for item in newest("backups")[:policy["backup_latest"]]:
        protect("backups", item["id"], "KEEP_POLICY", "ADDITIONAL_LATEST_BACKUP_POLICY", "proposed-policy")
    collected_at = _timestamp(snapshot.get("collected_at", snapshot.get("captured_at")))
    if policy["backup_daily_days"]:
        if collected_at is None:
            unknown.add("backups")
            warnings.append("COLLECTION_TIME_UNAVAILABLE_FOR_DAILY_POLICY")
        else:
            day = int(collected_at // 86400)
            seen_days = set()
            for item in newest("backups"):
                item_day = int(item["created_at"] // 86400)
                if day - policy["backup_daily_days"] < item_day <= day and item_day not in seen_days:
                    seen_days.add(item_day)
                    protect("backups", item["id"], "KEEP_POLICY", "ADDITIONAL_DAILY_BACKUP_POLICY", "proposed-policy")
    for item in nodes["backups"].values():
        if current and item.get("target_release") == current:
            protect("backups", item["id"], "KEEP_REQUIRED", "CURRENT_RELEASE_ROLLBACK_BACKUP", "current")
        if previous and item.get("target_release") == previous:
            protect("backups", item["id"], "KEEP_POLICY", "PREVIOUS_RELEASE_BACKUP", "previous-known-good")

    # Apply scoped uncertainty before following dependency closure. For example,
    # an opaque legacy backup protects all potentially referenced releases, whose
    # image contracts in turn protect their exact known image IDs.
    def uncertain_candidates():
        changed = False
        for category in unknown:
            for item in nodes[category].values():
                if item["classification"] == "DELETE_CANDIDATE":
                    review(item, "UNRESOLVED_REFERENCES_MAY_REQUIRE_OBJECT")
                    changed = True
        return changed

    uncertain_candidates()
    while True:
        before = [(category, item["id"], item["classification"]) for category in CATEGORIES for item in nodes[category].values()]
        for category in ("backups", "releases", "staging"):
            for item in nodes[category].values():
                if item["classification"] == "DELETE_CANDIDATE":
                    continue
                source = category + ":" + item["id"]
                classification = "KEEP_POLICY" if item["classification"] == "KEEP_POLICY" else "KEEP_REQUIRED"
                if category == "backups":
                    ref = item.get("previous_release")
                    if ref and not protect("releases", ref, "KEEP_REQUIRED", "RETAINED_BACKUP_RESTORE_RELEASE", source):
                        review(item, "RESTORE_RELEASE_UNAVAILABLE")
                    target = item.get("target_release")
                    if target and not protect("releases", target, "KEEP_REQUIRED", "RETAINED_BACKUP_TARGET_RELEASE", source):
                        review(item, "BACKUP_TARGET_RELEASE_UNAVAILABLE")
                if category == "releases":
                    ref = item.get("image_id") or item.get("immutable_image")
                    if not ref or not protect("images", ref, classification, "RETAINED_RELEASE_IMAGE", source):
                        review(item, "RELEASE_IMAGE_UNAVAILABLE")
                for ident in _strings(item.get("required_releases")) + _strings(item.get("release_refs")):
                    if not protect("releases", ident, "KEEP_REQUIRED", "RESTORE_OR_OPERATION_RELEASE_REFERENCE", source):
                        review(item, "REQUIRED_RELEASE_UNAVAILABLE")
                for ident in _strings(item.get("required_images")) + _strings(item.get("image_refs")):
                    if not protect("images", ident, "KEEP_REQUIRED", "RESTORE_OR_OPERATION_IMAGE_REFERENCE", source):
                        review(item, "REQUIRED_IMAGE_UNAVAILABLE")
                for ident in _strings(item.get("backup_refs")):
                    if not protect("backups", ident, "KEEP_REQUIRED", "RESTORE_OR_OPERATION_BACKUP_REFERENCE", source):
                        review(item, "REQUIRED_BACKUP_UNAVAILABLE")
                if category == "staging" and item.get("operation_sha") in nodes["releases"]:
                    protect("releases", item["operation_sha"], "KEEP_REQUIRED", "STAGING_OPERATION_RELEASE", source)
        uncertain_candidates()
        after = [(category, item["id"], item["classification"]) for category in CATEGORIES for item in nodes[category].values()]
        if before == after:
            break

    for category in CATEGORIES:
        for item in nodes[category].values():
            if item["classification"] == "DELETE_CANDIDATE":
                reason(item, "VERIFIED_MANAGED_OBJECT_WITH_NO_RETAINED_GRAPH_REFERENCE")
                reason(item, "OUTSIDE_ADDITIONAL_PROPOSED_RETENTION_POLICY")
            item["reasons"].sort()
            item["retained_by"].sort()

    ordered = {category: sorted(nodes[category].values(), key=lambda item: item["id"]) for category in CATEGORIES}
    summary = {}
    for category in CATEGORIES:
        summary[category] = {name: {"count": 0, "allocated_bytes": 0, "unknown_size_count": 0} for name in CLASSIFICATIONS}
        for item in ordered[category]:
            bucket = summary[category][item["classification"]]
            bucket["count"] += 1
            amount = _amount(item.get("size_bytes") if category == "images" else item.get("allocated_bytes"))
            if amount is None or item.get("size_complete") is not True:
                bucket["unknown_size_count"] += 1
            if amount is not None:
                bucket["allocated_bytes"] += amount
        if category == "images":
            for bucket in summary[category].values():
                bucket["size_semantics"] = "logical_image_bytes_not_physical_disk_usage"

    result = {
        "schema_version": 1, "read_only": True,
        "inventory": {field: copy.deepcopy(snapshot[field]) for field in (
            "captured_at", "captured_end_at", "collected_at", "current_release", "runtime_releases",
            "previous_known_good", "operation_observation", "collection_errors", "references_complete",
            "layer_size_basis", "docker_layers_complete", "build_cache_layer_pins_known", "filesystem_allocations_complete",
            "durable_operations"
        ) if field in snapshot},
        "plan_semantics": "joint_review_proposal_requires_fresh_reference_validation_before_any_future_removal",
        "policy": dict(policy, proposal=True, daily_timezone="UTC"),
        "nodes": ordered, "summary": summary,
        "references": sorted(references, key=lambda edge: (edge["from"], edge["to"], edge["reason"])),
        "warnings": sorted(set(warnings)), "unknown_reference_kinds": sorted(unknown),
        "recovery": _recovery(snapshot, ordered),
    }
    result["cleanup"] = build_cleanup_plan(snapshot, result)
    return result


def _recovery(snapshot, nodes):
    candidates = {category: [item for item in nodes[category] if item["classification"] == "DELETE_CANDIDATE"] for category in CATEGORIES}
    by_category = {category: sum(item["allocated_bytes"] for item in candidates[category]) for category in ("releases", "backups", "staging")}
    confirmed = {category: 0 for category in by_category}
    all_proven = False
    candidate_ids = {category + ":" + item["id"] for category in candidates for item in candidates[category]}
    if snapshot.get("filesystem_allocations_complete") is True and isinstance(snapshot.get("inode_allocations"), list):
        confirmed = {category: 0 for category in by_category}
        all_proven = True
        allocations = {}
        invalid = set()
        for allocation in snapshot["inode_allocations"]:
            if not isinstance(allocation, dict) or not isinstance(allocation.get("id"), str) or _amount(allocation.get("allocated_bytes")) is None:
                all_proven = False
                continue
            identity = allocation["id"]
            if (not isinstance(allocation.get("owners"), list) or
                    len(_strings(allocation["owners"])) != len(allocation["owners"])):
                all_proven = False
                invalid.add(identity)
                continue
            if identity in allocations and allocations[identity] != allocation:
                all_proven = False
                invalid.add(identity)
            allocations[identity] = allocation
        for identity, allocation in allocations.items():
            if identity in invalid:
                continue
            owners = set(_strings(allocation.get("owners")))
            if owners and owners <= candidate_ids and allocation.get("external_links") is False and allocation.get("open") is False:
                # Attribute once for presentation; the total is independent of
                # ordering even for a hardlink spanning release/backup objects.
                category = sorted(owners)[0].split(":", 1)[0]
                if category in confirmed:
                    confirmed[category] += allocation["allocated_bytes"]
    return {
        "filesystem_candidate_allocated_bytes": sum(by_category.values()),
        "filesystem_candidate_allocated_by_category": by_category,
        "filesystem_confirmed_bytes": sum(confirmed.values()),
        "filesystem_by_category": confirmed,
        "filesystem_estimate_status": "allocation_and_link_observation_complete" if all_proven else "partial_or_unverified_physical_recovery",
        "filesystem_caveat": "observed_allocations_are_not_a_reservation_or_guarantee_against_later_writes_or_open_files",
        "docker": _docker_recovery(snapshot, nodes["images"], candidates["images"]),
    }


def _docker_recovery(snapshot, images, candidates):
    sizes = {}
    complete = snapshot.get("docker_layers_complete") is True
    layer_inventory = snapshot.get("layers", [])
    if not isinstance(layer_inventory, list):
        complete = False
        layer_inventory = []
    for layer in layer_inventory:
        if isinstance(layer, dict) and isinstance(layer.get("id"), str) and _amount(layer.get("size_bytes")) is not None:
            if layer["id"] in sizes and sizes[layer["id"]] != layer["size_bytes"]:
                complete = False
            sizes[layer["id"]] = layer["size_bytes"]
        else:
            complete = False
    candidate_ids = {item["id"] for item in candidates}
    candidate_layers = set()
    retained_layers = set()
    for item in images:
        layers = item.get("layers")
        if not isinstance(layers, list) or item.get("layers_complete") is False or item.get("layer_sizes_complete") is False:
            complete = False
            continue
        if not layers and item.get("size_bytes") != 0:
            complete = False
        target = candidate_layers if item["id"] in candidate_ids else retained_layers
        for layer in layers:
            ident = layer.get("id") if isinstance(layer, dict) else layer
            if not isinstance(ident, str):
                complete = False
                continue
            target.add(ident)
            if isinstance(layer, dict):
                amount = _amount(layer.get("size_bytes"))
                if amount is None or (ident in sizes and sizes[ident] != amount):
                    complete = False
                else:
                    sizes[ident] = amount
    exclusive = candidate_layers - retained_layers
    if any(ident not in sizes for ident in exclusive):
        complete = False
    return {
        "candidate_image_count": len(candidates),
        "logical_image_bytes": sum(item["size_bytes"] for item in candidates),
        "unique_layer_upper_bound_bytes": sum(sizes[ident] for ident in exclusive) if complete else None,
        "exclusive_layer_count": len(exclusive) if complete else None,
        "guaranteed_reclaimable_bytes": 0,
        "estimate_status": "unique_layer_logical_upper_bound_only" if complete else "unknown_incomplete_layer_inventory",
        "caveat": "shared_layers_count_once; build_cache_and_other_engine_references_may_retain_exclusive_layers; logical_layer_bytes_are_not_physical_blocks",
    }
