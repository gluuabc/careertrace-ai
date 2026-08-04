import os
from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse

load_dotenv()


def get_llm(model_type="cheap"):
    if model_type == "cheap":
        model_id = os.getenv("BEDROCK_MODEL_CHEAP")

    elif model_type == "reasoning":
        model_id = os.getenv("BEDROCK_MODEL_REASONING")

    else:
        raise ValueError(f"Unknown model type: {model_type}")

    return ChatBedrockConverse(
        model=model_id,
        region_name=os.getenv("AWS_REGION"),
        temperature=0,
        max_tokens=4096,
    )