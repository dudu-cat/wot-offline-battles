import sys
def mock_get(*args, **kwargs): return None
import BigWorld
BigWorld.getWatcher = mock_get
BigWorld.setWatcher = mock_get
try:
    import account_helpers.AccountSettings as ah_as
    print 'ah_as is:', type(ah_as)
    if hasattr(ah_as, 'AccountSettings'):
        print 'ah_as.AccountSettings is:', type(ah_as.AccountSettings)
        print 'has getSettings?', hasattr(ah_as.AccountSettings, 'getSettings')
    print 'ah_as has getSettings?', hasattr(ah_as, 'getSettings')
except Exception as e:
    import traceback; traceback.print_exc()
