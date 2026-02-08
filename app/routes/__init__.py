from flask import Blueprint

routes_bp = Blueprint("routes", __name__)

from .dashboard import *  # noqa
from .items import *      # noqa
from .sales import *      # noqa
from .imports import *    # noqa
from .audit import *      # noqa
from .users import *      # noqa

