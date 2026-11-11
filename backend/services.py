"""组织现有 Python 函数，保持 ISTG/AEAD 数据不变。"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, Callable
import tempfile

import numpy as np
import istg

from .algorithms import validate_algorithm
from .runtime import APIError, FileStore, json_safe
from .schemas import (
    PayloadRequest, ExtractRequest, DetectRequest, BitplanesRequest, StatisticsRequest,
    QualityRequest, RecoveryRequest, BenchmarkRequest, RobustnessRequest, EvaluateRequest, CompareRequest,
)


class StudioService:
    def __init__(self, store: FileStore):
        self.store = store

    def image(self, identifier: str):
        item = self.store.get(identifier)
        if not item.format:
            raise APIError("invalid_image", "该文件不是已验证的图片", 422)
        return item

    def packet(self, request: PayloadRequest, *, capacity_only: bool = False) -> tuple[bytes, dict[str, Any]]:
        started = perf_counter()
        if request.data_type == "text":
            packet = istg.pack_istg("text", request.text)
            secret_bytes = len(request.text.encode("utf-8"))
        else:
            secret = self.image(request.secret_id or "")
            packet = istg.pack_istg("image", str(secret.path), image_mode="pixels" if request.image_mode == "bitplanes" else "file",
                                    pixel_bits=request.secret_bits)
            secret_bytes = len(istg.parse_istg(packet)["payload_data"]["data"])
        pack_seconds = perf_counter() - started
        plain_length = len(packet)
        started = perf_counter()
        if request.encryption != "none":
            password = request.password.get_secret_value() if request.password else ""
            if not capacity_only:
                if not password:
                    raise APIError("password_required", "启用加密时密码不能为空", 422)
                if request.confirm_password is None or request.confirm_password.get_secret_value() != password:
                    raise APIError("password_mismatch", "两次输入的密码不一致", 422)
            # 容量探测仅取真实编码长度，探测密文立即丢弃，正式嵌入绝不复用探测口令。
            packet = istg.encrypt_istg(packet, password if not capacity_only else "ISTG-capacity-length-probe", request.encryption)
        timings = {"pack_seconds": pack_seconds, "encrypt_seconds": perf_counter() - started}
        if len(packet) > 16 * 1024 * 1024:
            raise APIError("packet_too_large", "封装后数据超过 16 MiB", 413)
        return packet, {"secret_bytes": secret_bytes, "packet_bytes": len(packet),
                        "istg_overhead_bytes": plain_length - secret_bytes,
                        "encryption_overhead_bytes": len(packet) - plain_length, "timings": timings}

    def capacity(self, request: PayloadRequest) -> dict[str, Any]:
        cover = self.image(request.cover_id)
        params = validate_algorithm(request.algorithm, request.params)
        _, sizes = self.packet(request, capacity_only=True)
        return self._capacity(cover, request.algorithm, params, sizes)

    def _capacity(self, cover, algorithm: str, params: dict, sizes: dict) -> dict:
        capacity = istg.calculate_capacity(cover.path, algorithm, params)
        if capacity["status"] != "ok":
            raise APIError("unsupported", "算法没有可用容量接口", 422)
        available = capacity["payload_capacity_bytes"]
        used = sizes["packet_bytes"]
        return {"algorithm": algorithm, "cover": cover.public(), "capacity_bytes": available,
                "total_capacity_bits": capacity["total_capacity_bits"], "bpp": capacity["bpp"],
                **{key: value for key, value in sizes.items() if key != "timings"}, "used_bytes": used,
                "usage_percent": used * 100 / available if available else (0 if used == 0 else 100),
                "fits": available >= used, "output_format": "PNG",
                "notes": "容量为候选槽上限，变换域仍须通过最终像素完整校验；包含真实 ISTG 与加密开销。"}

    def embed(self, request: PayloadRequest, progress: Callable) -> dict[str, Any]:
        started = perf_counter()
        cover = self.image(request.cover_id)
        params = validate_algorithm(request.algorithm, request.params)
        progress(15, "正在准备信息")
        packet, sizes = self.packet(request)
        capacity = self._capacity(cover, request.algorithm, params, sizes)
        if not capacity["fits"]:
            raise APIError("capacity_exceeded", f"容量不足：需要 {len(packet)} 字节，可用 {capacity['capacity_bytes']} 字节", 422)
        progress(40, "正在嵌入信息")
        with tempfile.TemporaryDirectory(prefix="work_", dir=self.store.root) as directory:
            output = Path(directory) / "stego.png"
            adapter = istg.get_adapter(request.algorithm)
            begin = perf_counter()
            adapter.embed(cover.path, packet, output, params)
            embed_seconds = perf_counter() - begin
            begin = perf_counter()
            restored = adapter.extract(output, params)
            extract_seconds = perf_counter() - begin
            recovery = istg.calculate_recovery(packet, restored)
            if not recovery["exact_match"]:
                raise APIError("verification_failed", "隐藏数据未能完整恢复，结果未保存", 422)
            progress(80, "正在计算图像质量")
            quality = istg.calculate_quality(cover.path, output)
            stego = self.store.add(output.read_bytes(), "istg_stego.png", "image/png", image=True)
        return {"status": "ok", "algorithm": request.algorithm, "params": json_safe(params),
                "cover": cover.public(), "stego": stego.public(), "capacity": capacity, "quality": quality,
                "timings": {**sizes["timings"], "embed_seconds": embed_seconds, "extract_seconds": extract_seconds,
                            "total_seconds": perf_counter() - started}, "recovery": recovery,
                "encrypted": request.encryption != "none", "cipher": request.encryption, "packet_bytes": len(packet)}

    def detect(self, request: DetectRequest, progress: Callable) -> dict[str, Any]:
        image = self.image(request.image_id)
        candidates = request.parameter_candidates
        if len(candidates) > 10 or len(str(candidates)) > 100000:
            raise APIError("search_too_large", "参数候选范围过大", 422)
        progress(20, "正在检测隐藏信息")
        matches = istg.auto_detect(image.path, request.algorithms, candidates, request.max_attempts)
        return {"matches": json_safe(matches), "count": len(matches), "search_scope": "仅搜索已知算法和显式候选，不猜密码或未知散布密钥"}

    def extract(self, request: ExtractRequest, progress: Callable) -> dict[str, Any]:
        image = self.image(request.image_id)
        progress(20, "读取并验证数据包")
        password = request.password.get_secret_value() if request.password else None
        matches = []
        if request.mode == "auto":
            if len(str(request.parameter_candidates)) > 100000:
                raise APIError("search_too_large", "参数候选范围过大", 422)
            matches = istg.auto_detect(image.path, request.algorithms, request.parameter_candidates, request.max_attempts)
            if not matches:
                return {"status": "no_match", "matches": [], "authenticated": None, "error": "未找到有效数据包"}
            detection = matches[0]
        else:
            params = validate_algorithm(request.algorithm, request.params)
            detection = {"algorithm": request.algorithm, "params": params}
        progress(55, "正在提取信息")
        result = istg.auto_extract(image.path, detection, password)
        public = {key: value for key, value in result.items() if key != "data"}
        public["matches"] = json_safe(matches)
        public["encrypted"] = result.get("authenticated") is not None
        if result["status"] != "ok":
            return public
        data, kind, metadata = result["data"], result["data_type"], result.get("metadata") or {}
        progress(85, "正在整理提取结果")
        if kind == "text":
            item = self.store.add(data, "recovered_text.txt", "text/plain; charset=utf-8")
            public["file"] = item.public()
        elif kind == "file":
            item = self.store.add(data, metadata.get("filename", "recovered_file.bin"), "application/octet-stream")
            public["file"] = item.public()
            public["sha256"] = sha256(data).hexdigest()
        elif kind == "image_file":
            item = self.store.add(data, metadata.get("filename", "recovered_image.bin"), "application/octet-stream", image=True)
            public["file"] = public["preview"] = item.public()
            public["sha256"] = sha256(data).hexdigest()
        else:
            count = metadata["width"] * metadata["height"] * 3
            bits = metadata["bits_per_channel"]
            values = np.unpackbits(np.frombuffer(data, dtype=np.uint8), bitorder="big")[:count * bits].reshape(count, bits)
            weights = 1 << np.arange(bits - 1, -1, -1)
            pixels = ((values @ weights).astype(np.uint8) << (8 - bits)).reshape(metadata["height"], metadata["width"], 3)
            item = self.store.add_image(pixels, "recovered_bitplanes.png")
            public["file"] = public["preview"] = item.public()
            public["lossy"] = True
        return public

    def bitplanes(self, request: BitplanesRequest, progress: Callable) -> dict[str, Any]:
        image = self.image(request.image_id)
        progress(15, "正在生成位平面")
        planes = istg.get_bit_planes(image.path, request.channels, request.bits)
        reference = self.image(request.reference_id) if request.reference_id else None
        differences = istg.compare_bit_planes(reference.path, image.path, request.channels, request.bits) if reference else None
        records = []
        for channel, bits in planes.items():
            for bit, array in bits.items():
                item = {"channel": channel, "bit": bit,
                        "image": self.store.add_image(array, f"{channel}_bit{bit}.png").public()}
                if differences:
                    diff = differences[channel][bit]
                    item.update(reference=self.store.add_image(diff["original"], f"reference_{channel}_{bit}.png").public(),
                                difference=self.store.add_image(diff["difference"].astype(np.uint8) * 255, f"xor_{channel}_{bit}.png").public(),
                                changed_pixels=diff["changed_pixels"], change_ratio=diff["change_ratio"])
                records.append(item)
        return {"planes": records, "width": image.width, "height": image.height}

    def statistics(self, request: StatisticsRequest, progress: Callable) -> dict[str, Any]:
        image = self.image(request.image_id)
        reference = self.image(request.reference_id) if request.reference_id else None
        progress(20, "正在分析统计特征")
        return {"chi_square": istg.chi_square_analysis(image.path, request.channel, request.block_size),
                "rs": istg.rs_analysis(image.path, request.channel, request.group_size, tuple(request.mask)),
                "histogram": istg.histogram_analysis(image.path, reference.path if reference else None),
                "interpretation": "p 值是统计模型匹配程度，不是隐写概率；RS 分数不是嵌入率。"}

    def quality(self, request: QualityRequest, progress: Callable) -> dict[str, Any]:
        original, stego = self.image(request.original_id), self.image(request.stego_id)
        progress(20, "计算 MSE、PSNR、SSIM")
        return {"quality": istg.calculate_quality(original.path, stego.path), "original": original.public(), "stego": stego.public()}

    def recovery(self, request: RecoveryRequest, progress: Callable) -> dict[str, Any]:
        first, second = self.store.get(request.original_id), self.store.get(request.extracted_id)
        return istg.calculate_recovery(first.path.read_bytes(), second.path.read_bytes())

    def benchmark(self, request: BenchmarkRequest, progress: Callable) -> dict[str, Any]:
        image = self.image(request.image_id)
        params = validate_algorithm(request.algorithm, request.params)
        progress(20, "正在进行性能测试")
        return istg.benchmark_algorithm(image.path, request.payload_text.encode("utf-8"), request.algorithm, params, request.repeats)

    def robustness(self, request: RobustnessRequest, progress: Callable) -> dict[str, Any]:
        image = self.image(request.image_id)
        params = validate_algorithm(request.algorithm, request.params)
        if request.payload_file_id:
            payload = self.store.get(request.payload_file_id).path.read_bytes()
        elif request.payload_text is not None:
            payload = request.payload_text.encode("utf-8")
        else:
            payload = istg.get_adapter(request.algorithm).extract(image.path, params)
        progress(20, "正在测试扰动后的恢复效果")
        with tempfile.TemporaryDirectory(prefix="attacks_", dir=self.store.root) as directory:
            result = istg.robustness_test(image.path, payload, request.algorithm, params, request.attacks, directory)
            for row in result.get("results", []):
                path = Path(row.pop("output_path"))
                if path.is_file():
                    row["file"] = self.store.add(path.read_bytes(), path.name, "image/png", image=True).public()
        return result

    def evaluate(self, request: EvaluateRequest, progress: Callable) -> dict[str, Any]:
        originals = [self.image(identifier) for identifier in request.original_ids]
        stegos = [self.image(identifier) for identifier in request.stego_ids]
        progress(20, "正在评价检测结果")
        result = istg.evaluate_detection([item.path for item in originals], [item.path for item in stegos], request.detector, request.threshold)
        mapping = {str(item.path): item.public() for item in originals + stegos}
        for row in result["results"]:
            row["file"] = mapping.get(row.pop("path"))
        return result

    def compare(self, request: CompareRequest, progress: Callable) -> dict[str, Any]:
        if request.image_mode == "bitplanes" or "lsb_msb" in request.algorithms:
            raise APIError("incomparable_mode", "独立图片位平面模式不与通用算法混合比较", 422)
        cover = self.image(request.cover_id)
        packet, sizes = self.packet(request)
        rows = []
        algorithms = list(dict.fromkeys(request.algorithms))
        for index, algorithm in enumerate(algorithms):
            progress(10 + int(index / len(algorithms) * 80), f"正在对比 {algorithm}")
            row: dict[str, Any] = {"algorithm": algorithm, "packet_bytes": len(packet)}
            try:
                params_model = request.algorithm_params.get(algorithm, request.params)
                params = validate_algorithm(algorithm, params_model)
                capacity = self._capacity(cover, algorithm, params, sizes)
                row["capacity_bytes"] = capacity["capacity_bytes"]
                if not capacity["fits"]:
                    raise APIError("capacity_exceeded", "容量不足，未截断数据", 422)
                with tempfile.TemporaryDirectory(prefix="compare_", dir=self.store.root) as directory:
                    output = Path(directory) / "result.png"
                    adapter = istg.get_adapter(algorithm)
                    started = perf_counter()
                    adapter.embed(cover.path, packet, output, params)
                    embed_seconds = perf_counter() - started
                    started = perf_counter()
                    extracted = adapter.extract(output, params)
                    extract_seconds = perf_counter() - started
                    row.update(status="ok", quality=istg.calculate_quality(cover.path, output),
                               recovery=istg.calculate_recovery(packet, extracted),
                               timings={"embed_seconds": embed_seconds, "extract_seconds": extract_seconds},
                               stego=self.store.add(output.read_bytes(), f"{algorithm}.png", "image/png", image=True).public(),
                               benchmark=istg.benchmark_algorithm(cover.path, packet, algorithm, params, request.repeats))
                    if request.attacks:
                        attacked = istg.robustness_test(output, packet, algorithm, params, request.attacks, Path(directory) / "attacks")
                        for attack in attacked.get("results", []):
                            path = Path(attack.pop("output_path"))
                            if path.is_file():
                                attack["file"] = self.store.add(path.read_bytes(), path.name, "image/png", image=True).public()
                        row["robustness"] = attacked
            except (APIError, ValueError, TypeError, OSError, NotImplementedError) as exc:
                row.update(status=exc.code if isinstance(exc, APIError) else "failed", error=str(exc)[:200])
            rows.append(row)
        return {"rows": rows, "cover": cover.public(), "packet_bytes": len(packet), "cipher": request.encryption,
                "comparison_notes": "所有算法使用同一载体与同一完整加密包；超容不截断，计时包含真实图像读写；教学变体单独标注。"}
