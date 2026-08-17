"""#1513 AccountCommands.pyc values used by the minimal offline RPC surface."""

RES_FAILURE = -1
RES_SUCCESS = 0
RES_STREAM = 1

# Sent by the queue commands; below REQUEST_ID_UNRESERVED_MIN (20), so the
# response callback lookup in Account.onCmdResponse never matches it.
REQUEST_ID_NO_RESPONSE = 2

CMD_SYNC_DATA = 100
CMD_SYNC_SHOP = 300
CMD_REQ_SERVER_STATS = 501
CMD_SYNC_DOSSIERS = 600
CMD_ENQUEUE_RANDOM = 700
CMD_DEQUEUE_RANDOM = 701
CMD_SET_LANGUAGE = 1000
CMD_COMPLETE_TUTORIAL = 1150
CMD_ADD_INT_USER_SETTINGS = 1600
CMD_DEL_INT_USER_SETTINGS = 1601

# constants.pyc QUEUE_TYPE.RANDOMS, consumed by Account.onEnqueued/onDequeued.
QUEUE_TYPE_RANDOMS = 1
