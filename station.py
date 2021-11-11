import timaccop 
from Cryptodome.Cipher import AES
from collections import namedtuple
import struct
import os
import logging
from PIL import Image
import binascii

masterkey = bytearray.fromhex("D306D9348E29E5E358BF2934812002C1")

PORT = "/dev/ttyACM0"
EXTENDED_ADDRESS = [ 0x00, 0x12, 0x4B, 0x00, 0x14, 0xD9, 0x49, 0x35 ]
PANID = [ 0x47, 0x44 ]
CHANNEL = 11

PKT_ASSOC_REQ			= (0xF0)
PKT_ASSOC_RESP			= (0xF1)
PKT_CHECKIN				= (0xF2)
PKT_CHECKOUT			= (0xF3)
PKT_CHUNK_REQ			= (0xF4)
PKT_CHUNK_RESP			= (0xF5)


TagInfo = namedtuple('TagInfo', """
protoVer,
swVer,
hwType,
batteryMv,
rfu1,
screenPixWidth,
screenPixHeight,
screenMmWidth,
screenMmHeight,
compressionsSupported,
maxWaitMsec,
screenType,
rfu
""")

AssocInfo = namedtuple('AssocInfo', """
checkinDelay,
retryDelay,
failedCheckinsTillBlank,
failedCheckinsTillDissoc,
newKey,
rfu
""")

CheckinInfo = namedtuple('CheckinInfo', """
swVer,
hwType,
batteryMv,
lastPacketLQI,
lastPacketRSSI,
temperature,
rfu,
""")

PendingInfo = namedtuple('PendingInfo', """
imgUpdateVer,
imgUpdateSize,
osUpdateVer,
osUpdateSize,
rfu
""")

ChunkReqInfo = namedtuple('ChunkReqInfo', """
versionRequested,
offset,
len,
osUpdatePlz,
rfu,
""")

ChunkInfo = namedtuple('ChunkInfo', """
offset,
osUpdatePlz,
rfu,
""")

logging.basicConfig(format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

dsn = 0

def print(*args):
    msg = ""
    for arg in args:
        msg += str(arg) + " "
    logger.warning(msg)

def decrypt(hdr, enc, tag, nonce):
    cipher = AES.new(masterkey, AES.MODE_CCM, nonce, mac_len=4)
    cipher.update(hdr)
    plaintext = cipher.decrypt(enc)
    #print("rcvd_packet:", plaintext.hex())
    #print("rcvhdr:", hdr.hex())
    try:
        cipher.verify(tag)
        return plaintext
    except:
        return None

def send_data(dst, data):
    global dsn
    dsn += 1
    if dsn > 255:
        dsn = 0
    hdr = bytearray.fromhex("41cc")
    hdr.append(dsn)
    hdr.extend(PANID)
    hdr.extend(reversed(dst))
    hdr.extend(EXTENDED_ADDRESS)
    #print("hdr:", hdr.hex())

    cntr = bytearray.fromhex("00000000")
    nonce = bytearray.fromhex("00000000")
    nonce.extend(EXTENDED_ADDRESS)
    nonce.append(0)
    #print("nonce:", nonce.hex())

    cipher = AES.new(masterkey, AES.MODE_CCM, nonce, mac_len=4)
    cipher.update(hdr)
    ciphertext, tag = cipher.encrypt_and_digest(data)

    out = ciphertext+tag+cntr
    timaccop.mac_data_req(dst, PANID, 12, dsn, out)

def process_assoc(pkt, data):
    ti = TagInfo._make(struct.unpack('<BQHHBHHHHHHB11s',data))
    print(ti)

    ai = AssocInfo(
	    checkinDelay=900000, #check each 900sec 
	    retryDelay=1000, #retry delay 1000ms
	    failedCheckinsTillBlank=2,
	    failedCheckinsTillDissoc=0,
	    newKey=masterkey,
	    rfu=bytearray(8*[0])
    )
    print(ai)
    ai_pkt = bytearray([ PKT_ASSOC_RESP ]) + bytearray(struct.pack('<LLHH16s8s', *ai))

    send_data(pkt['src_add'], ai_pkt)

def get_image_data(client):
    filename = bytes(client).hex() + ".png"
    print("Reading image file:", filename)

    file_conv = filename + ".bmp"

    im = Image.open(filename)
    im_L = im.convert("1")
    im_L.save(file_conv)

    f = open(file_conv,mode='rb')
    image_data = f.read()
    f.close()

    return image_data

def process_checkin(pkt, data):
    ci = CheckinInfo._make(struct.unpack('<QHHBBB6s',data))
    print(ci)

    image_data = get_image_data(pkt['src_add'])
    imgVer = binascii.crc32(image_data)

    pi = PendingInfo(
        imgUpdateVer = imgVer, #increment on image change
        imgUpdateSize = len(image_data),
        osUpdateVer = ci.swVer,
        osUpdateSize = 0,
	    rfu=bytearray(8*[0])
    )
    print(pi)

    pi_pkt = bytearray([ PKT_CHECKOUT ]) + bytearray(struct.pack('<QLQL8s', *pi))

    send_data(pkt['src_add'], pi_pkt)

def process_download(pkt, data):
    cri = ChunkReqInfo._make(struct.unpack('<QLBB6s',data))
    print(cri)

    ci = ChunkInfo(
        offset = cri.offset,
        osUpdatePlz = 0,
        rfu = 0,
    )
    print(ci)

    image_data = get_image_data(pkt['src_add'])
    fdata = image_data[cri.offset:cri.offset+cri.len]

    outpkt = bytearray([ PKT_CHUNK_RESP ]) + bytearray(struct.pack('<LBB', *ci)) + bytearray(fdata)

    print("sending chunk", len(outpkt), outpkt[:10],"...")

    send_data(pkt['src_add'], outpkt)

def generate_pkt_header(pkt): #hacky- timaccop cannot provide header data
    bcast = True
    if pkt['dst_add'] == b'\xff\xff': #broadcast assoc
        hdr = bytearray.fromhex("01c8")
    else:
        hdr = bytearray.fromhex("41cc")
        bcast = False
    hdr.append(pkt['dsn'])
    hdr.extend(pkt['dst_pan_id'])
    hdr.extend(pkt['dst_add'])
    if bcast:
        hdr.extend(pkt['src_pan_id'])
    hdr.extend(reversed(pkt['src_add']))

    return hdr


def process_pkt(pkt):
    hdr = generate_pkt_header(pkt)

    nonce = bytearray(pkt['data'][-4:])
    nonce.extend(reversed(pkt['src_add']))
    nonce.extend(b'\x00')

    tag = pkt['data'][-8:-4]

    ciphertext = pkt['data'][:-8]

    plaintext = decrypt(hdr, ciphertext, tag, nonce)
    if not plaintext:
        print("data", pkt['data'].hex())
        print("hdr", hdr.hex())
        print("ciph", ciphertext.hex())
        print("nonce", nonce.hex())
        print("tag", tag.hex())
        print("packet is NOT authentic")
        return

    typ = plaintext[0]

    if typ == PKT_ASSOC_REQ:
        print("Got assoc request")
        process_assoc(pkt, plaintext[1:])
    elif typ == PKT_CHECKIN:
        print("Got checkin request")
        process_checkin(pkt, plaintext[1:])
    elif typ == PKT_CHUNK_REQ:
        print("Got chunk request")
        process_download(pkt, plaintext[1:])
    else:
        print("Unknown request", typ)

timaccop.init(PORT, PANID, CHANNEL, EXTENDED_ADDRESS, process_pkt)
print("Station started")

timaccop.run()

