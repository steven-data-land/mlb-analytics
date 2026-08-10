"""Storage backends for starter-record data: local CSV/Parquet, and S3.

Callers only ever depend on the StarterDataStorage ABC, so any backend is a
drop-in swap for any other.
"""

import io
import os
from abc import ABC, abstractmethod

import boto3
import botocore.exceptions
import pandas as pd
import streamlit as st

from . import config


def _load_aws_credentials_from_secrets() -> None:
    """Mirror AWS credentials from Streamlit's secrets manager into the
    environment, where boto3's default credential chain can find them.

    Locally, credentials come from `~/.aws/credentials` (via `aws
    configure`) and no `secrets.toml` exists at all -- `st.secrets` raises
    `StreamlitSecretNotFoundError` even just checking membership in that
    case, which this treats as "nothing to load" and leaves boto3's default
    chain untouched. On Streamlit Community Cloud there is no
    `~/.aws/credentials`, so the [aws] table configured in the app's Secrets
    settings is the only source -- this makes it visible to boto3.
    """
    try:
        if "aws" not in st.secrets:
            return
    except st.errors.StreamlitSecretNotFoundError:
        return

    aws_secrets = st.secrets["aws"]
    os.environ.setdefault("AWS_ACCESS_KEY_ID", aws_secrets["AWS_ACCESS_KEY_ID"])
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", aws_secrets["AWS_SECRET_ACCESS_KEY"])


_load_aws_credentials_from_secrets()


class StarterDataStorage(ABC):
    @abstractmethod
    def save(self, df: pd.DataFrame, path: str) -> None: ...

    @abstractmethod
    def load(self, path: str) -> pd.DataFrame: ...


class LocalCSVStorage(StarterDataStorage):
    def save(self, df: pd.DataFrame, path: str) -> None:
        df.to_csv(path, index=False)

    def load(self, path: str) -> pd.DataFrame:
        return pd.read_csv(path)


class LocalParquetStorage(StarterDataStorage):
    def save(self, df: pd.DataFrame, path: str) -> None:
        df.to_parquet(path, index=False)

    def load(self, path: str) -> pd.DataFrame:
        return pd.read_parquet(path)


class S3Storage(StarterDataStorage):
    """Parquet-in-S3 backend. `path` in save()/load() is the S3 object key
    (e.g. "starters/season=2024/data.parquet"), not a local filesystem path.

    Credentials come from boto3's default chain: locally, the
    ~/.aws/credentials file set up via `aws configure`; on Streamlit
    Community Cloud (no such file there), the app's Secrets settings via
    `_load_aws_credentials_from_secrets()` above. Never stored in this repo.
    """

    def __init__(self, bucket: str = config.S3_BUCKET, region: str = config.AWS_REGION):
        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region)

    def save(self, df: pd.DataFrame, path: str) -> None:
        buffer = io.BytesIO()
        df.to_parquet(buffer, index=False)
        buffer.seek(0)
        self._client.put_object(Bucket=self.bucket, Key=path, Body=buffer.getvalue())

    def load(self, path: str) -> pd.DataFrame:
        response = self._client.get_object(Bucket=self.bucket, Key=path)
        return pd.read_parquet(io.BytesIO(response["Body"].read()))

    def exists(self, path: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=path)
            return True
        except botocore.exceptions.ClientError as error:
            if error.response["Error"]["Code"] in ("404", "NoSuchKey"):
                return False
            raise
