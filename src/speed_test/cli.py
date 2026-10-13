import argparse
import http.client
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

import truststore

CHUNK_SIZE = 64 * 1024
BYTES_IN_MB = 1_000_000
DEFAULT_COUNT = 10
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class Measurement:
    size: int
    elapsed: float


def make_ssl_context(insecure: bool) -> ssl.SSLContext:
    """Проверяет сертификаты через системное хранилище ОС, как это делает браузер."""
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def fetch(url: str, timeout: float, context: ssl.SSLContext) -> Measurement:
    """Скачивает ресурс целиком и возвращает объём тела ответа и время запроса."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "speed-test/0.1",
            "Accept-Encoding": "identity",
            "Cache-Control": "no-cache",
        },
    )
    size = 0
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        while chunk := response.read(CHUNK_SIZE):
            size += len(chunk)
    return Measurement(size=size, elapsed=time.perf_counter() - start)


def describe_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code} {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        exc = exc.reason if isinstance(exc.reason, Exception) else exc
    if isinstance(exc, ssl.SSLCertVerificationError):
        return f"SSL-сертификат не прошёл проверку: {exc.verify_message}"
    if isinstance(exc, urllib.error.URLError):
        return str(exc.reason)
    return str(exc) or type(exc).__name__


def http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise argparse.ArgumentTypeError("нужен адрес вида http://... или https://...")
    return value


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return number


def positive_float(value: str) -> float:
    number = float(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("значение должно быть больше нуля")
    return number


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="speed-test",
        description="Замер скорости скачивания: несколько последовательных запросов к одному адресу.",
    )
    parser.add_argument("url", type=http_url, help="адрес тяжёлого файла, например картинки")
    parser.add_argument(
        "-n",
        "--count",
        type=positive_int,
        default=DEFAULT_COUNT,
        help=f"количество запросов (по умолчанию {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=positive_float,
        default=DEFAULT_TIMEOUT,
        help=f"таймаут одного запроса в секундах (по умолчанию {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "-k",
        "--insecure",
        action="store_true",
        help="не проверять SSL-сертификат сервера",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    context = make_ssl_context(args.insecure)
    print(f"Адрес: {args.url}")
    print(f"Запросов: {args.count}\n")

    measurements: list[Measurement] = []
    for number in range(1, args.count + 1):
        prefix = f"Запрос {number:>2}/{args.count}"
        try:
            measurement = fetch(args.url, args.timeout, context)
        except (OSError, http.client.HTTPException) as exc:
            print(f"{prefix}: ошибка: {describe_error(exc)}", file=sys.stderr)
            continue
        measurements.append(measurement)
        print(
            f"{prefix}: {measurement.size / BYTES_IN_MB:.2f} МБ "
            f"за {measurement.elapsed:.3f} с"
        )

    if not measurements:
        print("\nНи один запрос не выполнен успешно.", file=sys.stderr)
        return 1

    total_size = sum(m.size for m in measurements)
    total_time = sum(m.elapsed for m in measurements)
    average_time = total_time / len(measurements)
    speed = total_size / total_time / BYTES_IN_MB

    print()
    print(f"Успешных запросов:    {len(measurements)}/{args.count}")
    print(f"Среднее время:        {average_time:.3f} с")
    print(f"Скачано всего:        {total_size / BYTES_IN_MB:.2f} МБ")
    print(f"Скорость:             {speed:.2f} МБ/с ({speed * 8:.2f} Мбит/с)")
    return 0
