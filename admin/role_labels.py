from typing import Dict


ROLE_LABELS: Dict[str, str] = {
    "viewer": "閲覧のみ",
    "editor": "編集者",
    "guild_admin": "サーバー管理者",
    "admin": "管理者",
    "global_admin": "全体管理者",
}

ROLE_DESCRIPTIONS: Dict[str, str] = {
    "viewer": "設定を見るだけ。変更はできない。",
    "editor": "許可されたBot・サーバーの通常設定を変更できる。",
    "guild_admin": "許可されたBot・サーバーの管理者向け設定まで変更できる。",
    "admin": "管理者。現在の権限設計では既存互換用の表示名。",
    "global_admin": "全Bot・全サーバーを管理できる。ユーザー管理も可能。",
}


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role or "", role or "-")


def role_description(role: str) -> str:
    return ROLE_DESCRIPTIONS.get(role or "", "")
