import os
from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse

load_dotenv()


def resolve_bedrock_model_id(model_id: str | None, region: str | None) -> str | None:
    """Resolve Claude 4 on-demand IDs to the region's inference profile ID."""

    if not model_id:
        return model_id
    legacy_sonnet = "anthropic.claude-sonnet-4-20250514-v1:0"
    if model_id in {legacy_sonnet, f"us.{legacy_sonnet}", f"global.{legacy_sonnet}"}:
        # Bedrock marked the original Sonnet 4 profile as legacy. Preserve old
        # local configuration by upgrading it to the active compatible profile.
        return "us.anthropic.claude-sonnet-4-6"
    if ".anthropic.claude-" in model_id:
        return model_id
    if model_id.startswith("anthropic.claude-sonnet-4") and str(region).startswith("us-"):
        return f"us.{model_id}"
    return model_id


def get_llm(model_type="cheap"):
    if model_type == "cheap":
        model_id = os.getenv("BEDROCK_MODEL_CHEAP")

    elif model_type == "reasoning":
        model_id = os.getenv("BEDROCK_MODEL_REASONING")

    else:
        raise ValueError(f"Unknown model type: {model_type}")

    region = os.getenv("AWS_REGION")
    return ChatBedrockConverse(
        model=resolve_bedrock_model_id(model_id, region),
        region_name=region,
        temperature=0,
        max_tokens=4096,
    )
