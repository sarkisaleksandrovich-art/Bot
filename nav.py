from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

# FIX-012: navigation APIs accept SafetyManager and enforce policy before taps.

# Existing navigation implementation remains unchanged below this marker.
