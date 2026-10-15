from codesteer_atlas.config import GRAPH_NOISE_HUB_LABELS


def is_noise_hub(node: dict) -> bool:
    """Identifica ruído para o ranking de hubs e sua inspeção visual."""
    if node.get("kind") in {"section", "rationale"}:
        return True
    label = str(node.get("label") or "").casefold()
    return label in GRAPH_NOISE_HUB_LABELS
