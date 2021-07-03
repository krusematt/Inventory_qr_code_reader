import usb.core
import usb.util
import requests
import threading
import queue
import time
import logging
from subprocess import Popen

logging.basicConfig(format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S', filename='/var/log/qr-inventory.log', level=logging.DEBUG)


#from keyboard_alike import mapping

API_ENDPOINT = 'http://api/item/assign_storage'
SUCCESS_AUDIO_FILE_PATH = '/root/bell.wav'
ERROR_AUDIO_FILE_PATH = '/root/error.wav'

keys_page = [
    '', '', '', '',
    'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm',
    'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z',
    '1', '2', '3', '4', '5', '6', '7', '8', '9', '0', '\n', '^]', '^H',
    '^I', ' ', '-', '=', '[', ']', '\\', '>', ';', "'", '`', ',', '.',
    '/', 'CapsLock', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
    'PS', 'SL', 'Pause', 'Ins', 'Home', 'PU', '^D', 'End', 'PD', '->', '<-', '-v', '-^', 'NL',
    'KP/', 'KP*', '-', 'KP+', 'KPE', 'KP1', 'KP2', 'KP3', 'KP4', 'KP5', 'KP6', 'KP7', 'KP8',
    'KP9', 'KP0', '\\', 'App', 'Pow', 'KP=', 'F13', 'F14'
]

shift_keys_page = [
    '', '', '', '',
    'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M',
    'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z',
    '!', '@', '#', '$', '%', '^', '&', '*', '(', ')', '\n', '^]', '^H',
    '^I', ' ', '_', '+', '{', '}', '|', '<', ':', '"', '~', '<', '>',
    '?', 'CapsLock', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
    'PS', 'SL', 'Pause', 'Ins', 'Home', 'PU', '^D', 'End', 'PD', '->', '<-', '-v', '-^', 'NL',
    'KP/', 'KP*', 'KP-', 'KP+', 'KPE', 'KP1', 'KP2', 'KP3', 'KP4', 'KP5', 'KP6', 'KP7', 'KP8',
    'KP9', 'KP0', '|', 'App', 'Pow', 'KP=', 'F13', 'F14'
]


def map_character(c):
    return keys_page[c]


def chunk_data(data, chunks):
    for i in range(0, len(data), chunks):
        yield data[i:i + chunks]


def raw_to_key(key):
    if key[0] == 2:
        return shift_keys_page[key[1]]
    else:
        return keys_page[key[1]]




class DeviceException(Exception):
    pass


class ReadException(Exception):
    pass


class Reader(object):
    def __init__(self, vendor_id, product_id, data_size, chunk_size, should_reset, debug=False):
        """
        :param vendor_id: USB vendor id (check dmesg or lsusb under Linux)
        :param product_id: USB device id (check dmesg or lsusb under Linux)
        :param data_size: how much data is expected to be read - check experimentally
        :param chunk_size: chunk size like 6 or 8, check experimentally by looking on the raw output with debug=True
        :param should_reset: if true will also try to reset device preventing garbage reading.
        Doesn't work with all devices - locks them
        :param debug: if true will print raw data
        """
        self.interface = 0
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.data_size = data_size
        self.chunk_size = chunk_size
        self.should_reset = should_reset
        self.debug = debug
        self._device = None
        self._endpoint = None

    def initialize(self):
        self._device = usb.core.find(idVendor=self.vendor_id, idProduct=self.product_id)

        if self._device is None:
            raise DeviceException('No device found, check vendor_id and product_id')

        if self._device.is_kernel_driver_active(self.interface):
            try:
                self._device.detach_kernel_driver(self.interface)
            except usb.core.USBError as e:
                raise DeviceException('Could not detach kernel driver: %s' % str(e))

        try:
            self._device.set_configuration()
            if self.should_reset:
                self._device.reset()
        except usb.core.USBError as e:
            raise DeviceException('Could not set configuration: %s' % str(e))

        self._endpoint = self._device[0][(0, 0)][0]

    def read(self):
        data = []
        data_read = False

        while True:
            try:
                #data += self._device.read(self._endpoint.bEndpointAddress,
                #           self._endpoint.wMaxPacketSize)
                data += self._endpoint.read(self._endpoint.wMaxPacketSize)
                data_read = True
            except usb.core.USBError as e:
                #print(e)
                if e.args[0] == 110 and data_read:
                    if len(data) < self.data_size:
                        raise ReadException('Got %s bytes instead of %s - %s' % (len(data), self.data_size, str(data)))
                    else:
                        break

        if self.debug:
            print('Raw data', data)
        return self.decode_raw_data(data)

    def decode_raw_data(self, raw_data):
        data = self.extract_meaningful_data_from_chunk(raw_data)
        return self.raw_data_to_keys(data)

    def extract_meaningful_data_from_chunk(self, raw_data):
        shift_indicator_index = 0
        raw_key_value_index = 2
        for chunk in self.get_chunked_data(raw_data):
            yield (chunk[shift_indicator_index], chunk[raw_key_value_index])

    def get_chunked_data(self, raw_data):
        return chunk_data(raw_data, self.chunk_size)

    @staticmethod
    def raw_data_to_keys(extracted_data):
        return ''.join(map(raw_to_key, extracted_data))

    def disconnect(self):
        if self.should_reset:
            self._device.reset()
        usb.util.release_interface(self._device, self.interface)
        self._device.attach_kernel_driver(self.interface)




class BarCodeReader(Reader):
    """
    This class supports Lindy USB bar code scanner configured to work as a keyboard
    http://www.lindy.co.uk/accessories-c9/input-devices-c357/barcode-scanners-c360/barcode-scanner-ccd-usb-p1352
    """
    pass


class ProcessData():

    def __init__(self):
        logging.info('processor thread initializing')
        self.storage_code = ''
        self.inventory_code = ''

    def clear_codes(self):
        self.storage_code = False
        self.inventory_code = False
        logging.info('all codes cleared')

    def assign_inventory_to_storage(self):
        data = {'sto':self.storage_code, 'inv':self.inventory_code}
        logging.info('Assigning Inventory: %s    to Storage: %s' % (data['inv'], data['sto']))
        r = requests.put(url=API_ENDPOINT, data={'storage_id':self.storage_code, 'inventory_id':self.inventory_code})
        if r.status_code == 200:
            # play audio
            proc = Popen(['play %s' % SUCCESS_AUDIO_FILE_PATH], shell=True,
                         stdin=None, stdout=None, stderr=None, close_fds=True)
        else:
            # play audio
            proc = Popen(['play %s' % ERROR_AUDIO_FILE_PATH], shell=True,
                         stdin=None, stdout=None, stderr=None, close_fds=True)
        time.sleep(.5)
        print(data)
        self.clear_codes()

    def process_data_from_reader(self, data):
        codes = list(filter(None, data.split('\n')))
        for code in codes:
            logging.debug("raw code: %s", code)
            #print('raw code: ', code)
            # check what type of code this is.
            code_type = False
            code_value = False
            try:
                code_type, code_value = code.split('-')
            except Exception as e:
                print(e)
            #print('code type:', code_type, 'code:', code_value)
            if code_type == 'clr':
                self.clear_codes()
            if code_type == 'sto':
                self.storage_code = code_value
            elif code_type == 'inv':
                self.inventory_code = code_value

            if self.storage_code and self.inventory_code:
                self.assign_inventory_to_storage()




class Publish(threading.Thread):
    def __init__(self, q, *args, **kwargs):
        logging.info("Scanner Thread Initializing")
        self.q = q
        super().__init__(*args, **kwargs)
    def run(self):
        VENDOR_ID = 0x0483
        PRODUCT_ID = 0x5750
        reader = BarCodeReader(VENDOR_ID, PRODUCT_ID, 0, 8, should_reset=True, debug=True)
        reader.initialize()
        while True:
            try:
                self.q.put_nowait(reader.read().strip())
            except:
                try:
                    reader.initialize()
                except:
                    reader.disconnect()
            print('-----')


class Consume(threading.Thread):
    def __init__(self, q, *args, **kwargs):
        logging.info('Consumer thread initialized')
        self.q = q
        super().__init__(*args, **kwargs)
    def run(self):
        process = ProcessData()
        while True:
            try:
                work = self.q.get(timeout=3)  # 3s timeout
            except queue.Empty:
                continue
#             do whatever work you have to do on work
            logging.info('consumer received work from queue')
            print(work)
            process.process_data_from_reader(work)
            self.q.task_done()
#            else:
#                print('no work')



if __name__ == "__main__":
    logging.info("Script initializing")
    q = queue.Queue()
    Publish(q).start()
    Consume(q).start()
    q.join()  # blocks until the queue is empty.