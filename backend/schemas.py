"""HTTP 请求模型：只接收受控文件标识，不接收服务器路径。"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AlgorithmParams(StrictModel):
    bit_depth: int | None = Field(default=None, ge=1, le=4, strict=True)
    channels: str | None = Field(default=None, min_length=1, max_length=3)
    key: SecretStr | None = None
    edge_threshold: float | None = Field(default=None, ge=0, le=1000)
    strength: float | None = Field(default=None, ge=4, le=512)

    @field_validator("edge_threshold", "strength", mode="before")
    @classmethod
    def no_boolean_numbers(cls, value):
        if isinstance(value, bool):
            raise ValueError("数值参数不能是布尔值")
        return value

    @field_validator("channels")
    @classmethod
    def valid_channels(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.upper()
        if any(c not in "RGB" for c in value) or len(set(value)) != len(value):
            raise ValueError("通道必须由不重复的 R、G、B 组成")
        return value

    def native(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)
        if self.key is not None:
            data["key"] = self.key.get_secret_value()
        return data


class PayloadRequest(StrictModel):
    cover_id: str = Field(min_length=32, max_length=32)
    data_type: Literal["text", "image"] = "text"
    text: str = Field(default="", max_length=524288)
    secret_id: str | None = Field(default=None, min_length=32, max_length=32)
    image_mode: Literal["file", "bitplanes"] = "file"
    secret_bits: int = Field(default=2, ge=1, le=4, strict=True)
    algorithm: str = Field(default="sequential_lsb", min_length=1, max_length=40)
    params: AlgorithmParams = Field(default_factory=AlgorithmParams)
    encryption: Literal["none", "AES-256-GCM", "ChaCha20-Poly1305"] = "none"
    password: SecretStr | None = None
    confirm_password: SecretStr | None = None

    @model_validator(mode="after")
    def check_mode(self) -> "PayloadRequest":
        if self.data_type == "image" and self.secret_id is None:
            raise ValueError("图片模式必须上传秘密图片")
        if self.image_mode == "bitplanes":
            if self.data_type != "image" or self.algorithm != "lsb_msb":
                raise ValueError("位平面模式必须使用图片输入与独立 lsb_msb 算法")
        elif self.algorithm == "lsb_msb":
            raise ValueError("lsb_msb 仅用于独立图片位平面模式")
        return self


class DetectRequest(StrictModel):
    image_id: str = Field(min_length=32, max_length=32)
    algorithms: list[str] | None = Field(default=None, max_length=9)
    parameter_candidates: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=64, ge=1, le=64, strict=True)


class ExtractRequest(DetectRequest):
    mode: Literal["manual", "auto"] = "manual"
    algorithm: str = Field(default="sequential_lsb", min_length=1, max_length=40)
    params: AlgorithmParams = Field(default_factory=AlgorithmParams)
    password: SecretStr | None = None


class BitplanesRequest(StrictModel):
    image_id: str = Field(min_length=32, max_length=32)
    reference_id: str | None = Field(default=None, min_length=32, max_length=32)
    channels: list[str] = Field(default_factory=lambda: ["R", "G", "B"], min_length=1, max_length=3)
    bits: list[StrictInt] = Field(default_factory=lambda: [0], min_length=1, max_length=8)


class StatisticsRequest(StrictModel):
    image_id: str = Field(min_length=32, max_length=32)
    reference_id: str | None = Field(default=None, min_length=32, max_length=32)
    channel: Literal["R", "G", "B"] = "R"
    block_size: int = Field(default=4096, ge=256, le=1000000, strict=True)
    group_size: int = Field(default=4, ge=2, le=32, strict=True)
    mask: list[StrictInt] = Field(default_factory=lambda: [1, 0, 0, 1], min_length=2, max_length=32)


class QualityRequest(StrictModel):
    original_id: str = Field(min_length=32, max_length=32)
    stego_id: str = Field(min_length=32, max_length=32)


class RecoveryRequest(StrictModel):
    original_id: str = Field(min_length=32, max_length=32)
    extracted_id: str = Field(min_length=32, max_length=32)


class BenchmarkRequest(StrictModel):
    image_id: str = Field(min_length=32, max_length=32)
    payload_text: str = Field(default="ISTG Studio benchmark", max_length=262144)
    algorithm: str = Field(default="sequential_lsb", min_length=1, max_length=40)
    params: AlgorithmParams = Field(default_factory=AlgorithmParams)
    repeats: int = Field(default=3, ge=1, le=5, strict=True)


class RobustnessRequest(StrictModel):
    image_id: str = Field(min_length=32, max_length=32)
    payload_text: str | None = Field(default=None, max_length=262144)
    payload_file_id: str | None = Field(default=None, min_length=32, max_length=32)
    algorithm: str = Field(default="sequential_lsb", min_length=1, max_length=40)
    params: AlgorithmParams = Field(default_factory=AlgorithmParams)
    attacks: list[dict[str, Any]] = Field(default_factory=list, max_length=8)


class EvaluateRequest(StrictModel):
    original_ids: list[str] = Field(min_length=1, max_length=20)
    stego_ids: list[str] = Field(min_length=1, max_length=20)
    detector: Literal["chi_square", "rs"] = "chi_square"
    threshold: float = Field(default=0.5, ge=0, le=1)


class CompareRequest(PayloadRequest):
    algorithms: list[str] = Field(min_length=1, max_length=8)
    algorithm_params: dict[str, AlgorithmParams] = Field(default_factory=dict)
    repeats: int = Field(default=1, ge=1, le=3, strict=True)
    attacks: list[dict[str, Any]] = Field(default_factory=list, max_length=4)
