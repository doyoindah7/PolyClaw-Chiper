"""Inspect py-clob-client SDK — check order creation methods + version"""
import py_clob_client
print(f"Version: {py_clob_client.__version__ if hasattr(py_clob_client, '__version__') else 'unknown'}")
print(f"Location: {py_clob_client.__file__}")
print(f"Dir: {dir(py_clob_client)}")

from py_clob_client.client import ClobClient
print(f"\nClobClient methods:")
for m in sorted(dir(ClobClient)):
    if not m.startswith('_'):
        print(f"  {m}")

# Check order types
from py_clob_client.clob_types import OrderType
print(f"\nOrderType: {dir(OrderType)}")

# Check OrderArgs
from py_clob_client.clob_types import OrderArgs
import inspect
print(f"\nOrderArgs fields: {inspect.signature(OrderArgs.__init__) if hasattr(OrderArgs, '__init__') else 'no init'}")
print(f"OrderArgs attrs: {[f for f in dir(OrderArgs) if not f.startswith('_')]}")

# Check if there's a version or order builder
try:
    from py_clob_client.order_builder import constants
    print(f"\norder_builder constants: {dir(constants)}")
except:
    pass

# Check signature types
try:
    from py_clob_client.constants import POLYGON
    print(f"\nPOLYGON: {POLYGON}")
except:
    pass

# Look for POLY_GNOSIS_SAFE or POLY_PROXY
try:
    import py_clob_client.constants as c
    print(f"\nAll constants: {dir(c)}")
except:
    pass

# Check create_order method source
import inspect
src = inspect.getsource(ClobClient.create_order)
print(f"\ncreate_order source:\n{src[:2000]}")
