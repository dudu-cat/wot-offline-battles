import cPickle
import functools
import random
import zlib
from collections import namedtuple

import AccountCommands
import BigWorld
import game

from gui.mods.offhangar._constants import *
from gui.mods.offhangar.data import *
from gui.mods.offhangar.logging import *
from gui.mods.offhangar.server import *

RequestResult = namedtuple('RequestResult', ['resultID', 'errorStr', 'data'])

def baseRequest(cmdID):
	def wrapper(func):
		def requester(requestID, *args):
			result = func(requestID, *args)
			return requestID, result.resultID, result.errorStr, result.data
		BASE_REQUESTS[cmdID] = requester
		return func
	return wrapper

def packStream(requestID, data):
	data = zlib.compress(cPickle.dumps(data))
	desc = cPickle.dumps((len(data), zlib.crc32(data)))
	return functools.partial(game.onStreamComplete, requestID, desc, data)

@baseRequest(AccountCommands.CMD_REQ_SERVER_STATS)
def serverStats(requestID, int1, int2, int3):
	BigWorld.player().receiveServerStats({
		'clusterCCU': 155000 * (1 - random.uniform(0.0, 0.15)),
		'regionCCU': 815000 * (1 - random.uniform(0.0, 0.15))
	})
	return RequestResult(AccountCommands.RES_SUCCESS, '', None)

@baseRequest(AccountCommands.CMD_COMPLETE_TUTORIAL)
def completeTutorial(requestID, revision, dataLen, dataCrc):
	return RequestResult(AccountCommands.RES_SUCCESS, '', {})

@baseRequest(AccountCommands.CMD_SYNC_DATA)
def syncData(requestID, revision, crc, _):
	data = {'rev': revision + 2, 'prevRev': revision}
	data.update(getOfflineInventory())
	data.update(getOfflineStats())
	data.update(getOfflineQuestsProgress())
	return RequestResult(AccountCommands.RES_SUCCESS, '', data)

@baseRequest(AccountCommands.CMD_SYNC_SHOP)
def syncShop(requestID, revision, dataLen, dataCrc):
	data = {'rev': revision + 2, 'prevRev': revision}
	BigWorld.callback(REQUEST_CALLBACK_TIME, packStream(requestID, data))
	return RequestResult(AccountCommands.RES_STREAM, '', None)

@baseRequest(AccountCommands.CMD_SYNC_DOSSIERS)
def syncDossiers(requestID, revision, maxChangeTime, _):
	BigWorld.callback(REQUEST_CALLBACK_TIME, packStream(requestID, (revision + 2, [])))
	return RequestResult(AccountCommands.RES_STREAM, '', None)

@baseRequest(AccountCommands.CMD_SET_LANGUAGE)
def setLanguage(requestID, language):
	BigWorld.callback(REQUEST_CALLBACK_TIME, packStream(requestID, (language)))
	return RequestResult(AccountCommands.RES_STREAM, '', None)