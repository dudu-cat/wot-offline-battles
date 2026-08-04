import BigWorld, Math
import pprint
try:
    mp = Math.MatrixProduct()
    print("Has inverse?", hasattr(mp, 'inverse'))
    if hasattr(mp, 'inverse'):
        print("Type of inverse:", type(mp.inverse))
except Exception as e:
    print("Error:", str(e))

try:
    print("Has invert?", hasattr(mp, 'invert'))
except Exception as e:
    print("Error:", str(e))
