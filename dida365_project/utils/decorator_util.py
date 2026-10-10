import traceback
import logging
from typing import Callable
from utils.logging_util import log_event


def ensure_run_retry(run: Callable):
    def wrapper(self, *args):
        try:
            run(self, *args)
        except BaseException as error:
            log_event("dida", "operation_retry", level=logging.ERROR, error=error, operation=run.__name__, next_attempt=2)
            traceback.print_exc()
            self.dida.get_latest_data()
            run(self, *args)
    return wrapper
