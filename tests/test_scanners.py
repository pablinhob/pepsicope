from periscope.scanners import _composer, _gomod, _pkg_json, _pyproject, _requirements


def test_package_json():
    deps = _pkg_json('{"dependencies": {"react": "^19"}, "devDependencies": {"vite": "^7"}}')
    assert deps == {"react": "^19", "vite": "^7 (dev)"}


def test_composer_skips_platform_packages():
    deps = _composer('{"require": {"php": "^8.3", "ext-json": "*", "guzzlehttp/guzzle": "^7"}}')
    assert deps == {"guzzlehttp/guzzle": "^7"}


def test_requirements():
    deps = _requirements("Django>=5.0  # web\n-r base.txt\nboto3\nuvicorn[standard]==0.30\n")
    assert deps == {"django": ">=5.0", "boto3": "*", "uvicorn": "==0.30"}


def test_pyproject():
    deps = _pyproject('[project]\ndependencies = ["httpx>=0.28", "fastapi"]\n')
    assert set(deps) == {"httpx", "fastapi"}


def test_gomod():
    deps = _gomod(
        "module x\n\nrequire (\n\tgithub.com/gin-gonic/gin v1.10.0\n)\nrequire golang.org/x/net "
        "v0.1.0\n"
    )
    assert deps == {"github.com/gin-gonic/gin": "v1.10.0", "golang.org/x/net": "v0.1.0"}
