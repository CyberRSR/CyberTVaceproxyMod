import gevent
from gevent.event import AsyncResult
from gevent.event import Event
import telnetlib
import logging
from acemessages import *


class AceException(Exception):

    '''
    Exception from AceClient
    '''
    pass


class AceClient(object):

    def __init__(self, host, port, connect_timeout=5, result_timeout=10, debug=logging.ERROR):
        # Receive buffer
        self._recvbuffer = None
        # Stream URL
        self._url = None
        # Ace stream socket
        self._socket = None
        # Result timeout
        self._resulttimeout = result_timeout
        # Shutting down flag
        self._shuttingDown = Event()
        # Product key
        self._product_key = None
        # Debug level
        self._debug = debug
        # Current STATUS
        self._status = None
        # Current STATE
        self._state = None
        # Current AUTH
        self._auth = None
        self._gender = None
        self._age = None
        # Result (Created with AsyncResult() on call)
        self._result = AsyncResult()
        self._authevent = Event()
        # Result for getURL()
        self._urlresult = AsyncResult()
        # Event for resuming from PAUSE
        self._resumeevent = Event()
        #PID video info
        self._pidinfo = None

        # Logger
        logger = logging.getLogger('AceClient_init')

        try:
            self._socket = telnetlib.Telnet(host, port, connect_timeout)
            logger.info("Successfully connected with Ace!")
        except Exception as e:
            raise AceException(
                "Socket creation error! Ace is not running? " + repr(e))

        # Spawning recvData greenlet
        gevent.spawn(self._recvData)
        gevent.sleep()

    def __del__(self):
        # Destructor just calls destroy() method
        self.destroy()

    def destroy(self):
        '''
        AceClient Destructor
        '''
        if self._shuttingDown.isSet():
        # Already in the middle of destroying
            return

        # Logger
        logger = logging.getLogger("AceClient_destroy")
        # We should resume video to prevent read greenlet deadlock
        self._resumeevent.set()
        # And to prevent getUrl deadlock
        self._urlresult.set()

        # Trying to disconnect
        try:
            logger.debug("Destroying client...")
            self._shuttingDown.set()
            self._write(AceMessage.request.SHUTDOWN)
        except:
            # Ignore exceptions on destroy
            pass
        finally:
            self._shuttingDown.set()

    def _write(self, message):
        try:
            self._socket.write(message + "\r\n")
        except EOFError as e:
            raise AceException("Write error! " + repr(e))

    def aceInit(self, gender=AceConst.SEX_MALE, age=AceConst.AGE_18_24, product_key=None, pause_delay=0):
        self._product_key = product_key
        self._gender = gender
        self._age = age
        # PAUSE/RESUME delay
        self._pausedelay = pause_delay

        # Logger
        logger = logging.getLogger("AceClient_aceInit")

        # Sending HELLO
        self._write(AceMessage.request.HELLO)
        if not self._authevent.wait(self._resulttimeout):
            errmsg = "Authentication timeout. Wrong key?"
            logger.error(errmsg)
            raise AceException(errmsg)
            return

        if not self._auth:
            errmsg = "Authentication error. Wrong key?"
            logger.error(errmsg)
            raise AceException(errmsg)
            return

        logger.debug("aceInit ended")

    def START(self, datatype, value):
        '''
        Start video method
        '''

        # Logger
        logger = logging.getLogger("AceClient_START")

        self._result = AsyncResult()
        self._urlresult = AsyncResult()

        self._write(AceMessage.request.START(datatype.upper(), value))

        try:
            if not self._result.get(timeout=self._resulttimeout):
                errmsg = "START error!"
                logger.error(errmsg)
                raise AceException(errmsg)
        except gevent.Timeout:
            errmsg = "START timeout!"
            logger.error(errmsg)
            raise AceException(errmsg)

    def LOADASYNC(self, zreqtype, vpid):
        '''
        Get video info
        '''
        
        # Logger
        logger = logging.getLogger("AceClient_LOADASYNC")
		
        if zreqtype == 'pid':
            tmess = AceMessage.request.LOADASYNC('PID', '123456', {'content_id': vpid})
        elif zreqtype == 'torrent':
            tmess = AceMessage.request.LOADASYNC('TORRENT', '123456', {'url': vpid})
		
        self._write(tmess) 
        logger.debug('mess = ' + tmess)

        try:
            if not self._result.get(timeout=self._resulttimeout):
                errmsg = "LOADASYNC error!"
                logger.error(errmsg)
                raise AceException(errmsg)
        except gevent.Timeout:
            errmsg = "LOADASYNC timeout!"
            logger.error(errmsg)
            raise AceException(errmsg)
			
    def getLOADRESP(self):
        '''
        Get LOADRESP string
        '''
        return self._pidinfo
        
    def getUrl(self, timeout=40):
        # Logger
        logger = logging.getLogger("AceClient_getURL")

        try:
            res = self._urlresult.get(timeout=timeout)
            return res
        except gevent.Timeout:
            errmsg = "getURL timeout!"
            logger.error(errmsg)
            raise AceException(errmsg)

    def getPlayEvent(self, timeout=None):
        '''
        Blocking while in PAUSE, non-blocking while in RESUME
        '''
        self._resumeevent.wait(timeout=timeout)
        return

    def _recvData(self):
        '''
        Data receiver method for greenlet
        '''
        logger = logging.getLogger('AceClient_recvdata')

        while True:
            gevent.sleep()
            try:
                self._recvbuffer = self._socket.read_until("\r\n")
                self._recvbuffer = self._recvbuffer.strip()
            except:
                # If something happened during read, abandon reader.
                if not self._shuttingDown.isSet():
                    logger.error("Exception at socket read")
                    self._shuttingDown.set()
                return

            if self._recvbuffer:
                # Parsing everything only if the string is not empty
                if self._recvbuffer.startswith(AceMessage.response.HELLO):
                    # Parse HELLO
                    if 'key=' in self._recvbuffer:
                        self._request_key = self._recvbuffer.split()[
                            2].split('=')[1]
                        try:
                            self._write(AceMessage.request.READY_key(
                                self._request_key, self._product_key,
                                self._resulttimeout))
                        except urllib2.URLError as e:
                            logger.error("Can't connect to keygen server! " + \
                                repr(e))
                            self._auth = False
                            self._authevent.set()
                            
                        self._request_key = None
                    else:
                        self._write(AceMessage.request.READY_nokey)

                elif self._recvbuffer.startswith(AceMessage.response.NOTREADY):
                    # NOTREADY
                    logger.error("Ace is not ready. Wrong auth?")
                    self._auth = False
                    self._authevent.set()
                    

                elif self._recvbuffer.startswith(AceMessage.response.START):
                    # START
                    try:
                        self._url = self._recvbuffer.split()[1]
                        self._urlresult.set(self._url)
                        self._resumeevent.set()
                    except IndexError as e:
                        self._url = None
						
                elif self._recvbuffer.startswith(AceMessage.response.LOADRESP):
                    # LOADASYNC
                    self._pidinfo = self._recvbuffer.split()[5]
                    logger.debug('pidinfo = ' + self._pidinfo)					
                    
                elif self._recvbuffer.startswith(AceMessage.response.STOP):
                    pass

                elif self._recvbuffer.startswith(AceMessage.response.SHUTDOWN):
                    logger.debug("Got SHUTDOWN from engine")
                    self._socket.close()
                    return

                elif self._recvbuffer.startswith(AceMessage.response.AUTH):
                    try:
                        self._auth = self._recvbuffer.split()[1]
                        # Send USERDATA here
                        self._write(
                            AceMessage.request.USERDATA(self._gender, self._age))
                    except:
                        pass
                    self._authevent.set()

                elif self._recvbuffer.startswith(AceMessage.response.GETUSERDATA):
                    raise AceException("You should init me first!")

                elif self._recvbuffer.startswith(AceMessage.response.STATE):
                    self._state = self._recvbuffer.split()[1]

                elif self._recvbuffer.startswith(AceMessage.response.STATUS):
                    self._tempstatus = self._recvbuffer.split()[1].split(';')[0]
                    if self._tempstatus != self._status:
                        self._status = self._tempstatus
                        logger.debug("STATUS changed to " + self._status)

                    if self._status == 'main:err':
                        logger.error(
                            self._status + ' with message ' + self._recvbuffer.split(';')[2])
                        self._result.set_exception(
                            AceException(self._status + ' with message ' + self._recvbuffer.split(';')[2]))
                        self._urlresult.set_exception(
                            AceException(self._status + ' with message ' + self._recvbuffer.split(';')[2]))
                    elif self._status == 'main:starting':
                        self._result.set(True)

                elif self._recvbuffer.startswith(AceMessage.response.PAUSE):
                    logger.debug("PAUSE event")
                    self._resumeevent.clear()

                elif self._recvbuffer.startswith(AceMessage.response.RESUME):
                    logger.debug("RESUME event")
                    gevent.sleep(self._pausedelay)
                    self._resumeevent.set()
