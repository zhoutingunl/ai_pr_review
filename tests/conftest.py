"""测试公共 fixture。"""
import os
import tempfile

import pytest

from db import Store


@pytest.fixture
def store():
    s = Store(os.path.join(tempfile.mkdtemp(), "test.db"))
    yield s
    s.close()
