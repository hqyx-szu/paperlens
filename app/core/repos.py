from enum import StrEnum


class RepoVisibility(StrEnum):
    public = "public"
    private = "private"


class RepoScope(StrEnum):
    mine = "mine"
    public = "public"


class RepoPaperAddReason(StrEnum):
    duplicate = "duplicate"
    not_ready = "not_ready"
    archived = "archived"
    not_found = "not_found"
