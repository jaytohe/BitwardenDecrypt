#!/usr/bin/env python3

# Copyright © 2020-2021 Gurpreet Kang
# All rights reserved.
#
# Released under the "GNU General Public License v3.0". Please see the LICENSE.
# https://github.com/GurpreetKang/BitwardenDecrypt


# BitwardenDecrypt
#
# Decrypts an encrypted Bitwarden data.json file (from the desktop App).
#
# To determine the location of the data.json file see:
# https://bitwarden.com/help/article/where-is-data-stored-computer/
#
#
# Outputs JSON containing:
#  - Logins
#  - Cards
#  - Secure Notes
#  - Identities
#  - Folders
# 
#
# Usage: ./BitwardenDecrypt.py  (reads data.json from current directory)
#        or
#        ./BitwardenDecrypt.py inputfile
# Password: (Enter Password)
#


import base64
import getpass
import json
import re
import sys


# This script depends on the 'cryptography' package
# pip install cryptography
try:
    from cryptography.hazmat.primitives import hashes, hmac
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.asymmetric import rsa, padding as asymmetricpadding
    from cryptography.hazmat.primitives.serialization import load_der_private_key
except ModuleNotFoundError:
    print("This script depends on the 'cryptography' package")
    print("pip install cryptography")
    exit(1)


def getBitwardenSecrets(email, password, kdfIterations, encKey, encPrivateKey):
    BitwardenSecrets = {}
    BitwardenSecrets['email']           = email
    BitwardenSecrets['kdfIterations']   = kdfIterations
    BitwardenSecrets['MasterPassword']  = password
    BitwardenSecrets['ProtectedSymmetricKey'] = encKey
    BitwardenSecrets['ProtectedRSAPrivateKey'] = encPrivateKey

    
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bytes(BitwardenSecrets['email'], 'utf-8'),
        iterations=BitwardenSecrets['kdfIterations'],
        backend=default_backend()
        )
    BitwardenSecrets['MasterKey']       = kdf.derive(BitwardenSecrets['MasterPassword'])
    BitwardenSecrets['MasterKey_b64']   = base64.b64encode(BitwardenSecrets['MasterKey']).decode('utf-8')


    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=bytes(BitwardenSecrets['MasterPassword']),
        iterations=1,
        backend=default_backend()
        )
    BitwardenSecrets['MasterPasswordHash']  = base64.b64encode(kdf.derive(BitwardenSecrets['MasterKey'])).decode('utf-8')


    hkdf = HKDFExpand(
        algorithm=hashes.SHA256(),
        length=32,
        info=b"enc",
        backend=default_backend()
        )
    BitwardenSecrets['StretchedEncryptionKey']      = hkdf.derive(BitwardenSecrets['MasterKey'])
    BitwardenSecrets['StretchedEncryptionKey_b64']  = base64.b64encode(BitwardenSecrets['StretchedEncryptionKey']).decode('utf-8')

    hkdf = HKDFExpand(
        algorithm=hashes.SHA256(),
        length=32,
        info=b"mac",
        backend=default_backend()
        )
    BitwardenSecrets['StretchedMacKey']     = hkdf.derive(BitwardenSecrets['MasterKey'])
    BitwardenSecrets['StretchedMacKey_b64'] = base64.b64encode(BitwardenSecrets['StretchedMacKey']).decode('utf-8')

    BitwardenSecrets['StretchedMasterKey']      = BitwardenSecrets['StretchedEncryptionKey'] + BitwardenSecrets['StretchedMacKey']
    BitwardenSecrets['StretchedMasterKey_b64']  = base64.b64encode(BitwardenSecrets['StretchedMasterKey']).decode('utf-8')

    BitwardenSecrets['GeneratedSymmetricKey'], \
    BitwardenSecrets['GeneratedEncryptionKey'], \
    BitwardenSecrets['GeneratedMACKey']             = decryptMasterEncryptionKey(BitwardenSecrets['ProtectedSymmetricKey'], BitwardenSecrets['StretchedEncryptionKey'], BitwardenSecrets['StretchedMacKey'])
    BitwardenSecrets['GeneratedSymmetricKey_b64']   = base64.b64encode(BitwardenSecrets['GeneratedSymmetricKey']).decode('utf-8')
    BitwardenSecrets['GeneratedEncryptionKey_b64']  = base64.b64encode(BitwardenSecrets['GeneratedEncryptionKey']).decode('utf-8')
    BitwardenSecrets['GeneratedMACKey_b64']         = base64.b64encode(BitwardenSecrets['GeneratedMACKey']).decode('utf-8')


    BitwardenSecrets['RSAPrivateKey'] = decryptRSAPrivateKey(BitwardenSecrets['ProtectedRSAPrivateKey'], \
                                                            BitwardenSecrets['GeneratedEncryptionKey'], \
                                                            BitwardenSecrets['GeneratedMACKey'])

    return(BitwardenSecrets)



def decryptMasterEncryptionKey(CipherString, masterkey, mastermac):
    encType     = int(CipherString.split(".")[0])   # Not Currently Used, Assuming EncryptionType: 2
    iv          = base64.b64decode(CipherString.split(".")[1].split("|")[0])
    ciphertext  = base64.b64decode(CipherString.split(".")[1].split("|")[1])
    mac         = base64.b64decode(CipherString.split(".")[1].split("|")[2])


    # Calculate CipherString MAC
    h = hmac.HMAC(mastermac, hashes.SHA256(), backend=default_backend())
    h.update(iv)
    h.update(ciphertext)
    calculatedMAC = h.finalize()
    
    if mac != calculatedMAC:
        print("ERROR: MAC did not match. Master Encryption Key was not decrypted.")
        exit(1)


    unpadder    = padding.PKCS7(128).unpadder()
    cipher      = Cipher(algorithms.AES(masterkey), modes.CBC(iv), backend=default_backend())
    decryptor   = cipher.decryptor() 
    decrypted   = decryptor.update(ciphertext) + decryptor.finalize()

    try:
        cleartext = unpadder.update(decrypted) + unpadder.finalize()
    except:
        print()
        print("Wrong Password. Could Not Decode Protected Symmetric Key.")
        exit(1)

    stretchedmasterkey  = cleartext
    enc                 = stretchedmasterkey[0:32]
    mac                 = stretchedmasterkey[32:64]

    return([stretchedmasterkey,enc,mac])


def decryptRSAPrivateKey(CipherString, key, mackey):
    if not CipherString:
        return(None)

    
    encType     = int(CipherString.split(".")[0])   # Not Currently Used, Assuming EncryptionType: 2
    iv          = base64.b64decode(CipherString.split(".")[1].split("|")[0])
    ciphertext  = base64.b64decode(CipherString.split(".")[1].split("|")[1])
    mac         = base64.b64decode(CipherString.split(".")[1].split("|")[2])


    # Calculate CipherString MAC
    h = hmac.HMAC(mackey, hashes.SHA256(), backend=default_backend())
    h.update(iv)
    h.update(ciphertext)
    calculatedMAC = h.finalize()

    if mac == calculatedMAC:       
        unpadder    = padding.PKCS7(128).unpadder()
        cipher      = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor   = cipher.decryptor() 
        decrypted   = decryptor.update(ciphertext) + decryptor.finalize()
        cleartext   = unpadder.update(decrypted) + unpadder.finalize()

        return(cleartext)

    else:
        return("ERROR: MAC did not match. RSA Private Key not decrypted.")



def decryptCipherString(CipherString, key, mackey):
    if not CipherString:
        return(None)

    
    encType     = int(CipherString.split(".")[0])   # Not Currently Used, Assuming EncryptionType: 2
    iv          = base64.b64decode(CipherString.split(".")[1].split("|")[0])
    ciphertext  = base64.b64decode(CipherString.split(".")[1].split("|")[1])
    mac         = base64.b64decode(CipherString.split(".")[1].split("|")[2])


    # Calculate CipherString MAC
    h = hmac.HMAC(mackey, hashes.SHA256(), backend=default_backend())
    h.update(iv)
    h.update(ciphertext)
    calculatedMAC = h.finalize()

    if mac == calculatedMAC:
        unpadder    = padding.PKCS7(128).unpadder()
        cipher      = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor   = cipher.decryptor() 
        decrypted   = decryptor.update(ciphertext) + decryptor.finalize()
        cleartext   = unpadder.update(decrypted) + unpadder.finalize()

        return(cleartext.decode('utf-8'))

    else:
        return("ERROR: MAC did not match. CipherString not decrypted.")


def decryptRSA(CipherString, key):
    encType     = int(CipherString.split(".")[0])   # Not Currently Used, Assuming EncryptionType: 4
    ciphertext  = base64.b64decode(CipherString.split(".")[1].split("|")[0])
    private_key = load_der_private_key(key, password=None, backend=default_backend())

    plaintext = private_key.decrypt(ciphertext, asymmetricpadding.OAEP(mgf=asymmetricpadding.MGF1(algorithm=hashes.SHA1()), \
                                                                        algorithm=hashes.SHA1(), \
                                                                        label=None))

    return(plaintext)


def decryptBitwardenJSON(inputfile):
    BitwardenSecrets = {}
    decryptedEntries = {}

    try:
        with open(inputfile) as f:
            datafile = json.load(f)
    except:
        print("ERROR: " + inputfile + " not found.")
        exit(1)

    BitwardenSecrets  = getBitwardenSecrets(datafile["userEmail"], \
                                            getpass.getpass().encode("utf-8"), \
                                            datafile["kdfIterations"], \
                                            datafile["encKey"], \
                                            datafile["encPrivateKey"]    )

    


    BitwardenSecrets['OrgSecrets'] = {}
    encOrgKeys = list(datafile["encOrgKeys"])

    for i in encOrgKeys:
        BitwardenSecrets['OrgSecrets'][i] = decryptRSA(datafile["encOrgKeys"][i], BitwardenSecrets['RSAPrivateKey'])

    
    regexPattern = re.compile(r"\d\.[^,]+\|[^,]+=+")
    
    for a in datafile:

        if a.startswith('folders_'):
            group = "folders"
        elif a.startswith('ciphers_'):
            group = "items"
        elif a.startswith('organizations_'):
            group = "organizations"
        elif a.startswith('collections_'):
            group = "collections"
        else:
            group = None


        if group:
            groupData = json.loads(json.dumps(datafile[a]))
            groupItemsList = []
    
            for b in groupData.items():
                groupEntries = json.loads(json.dumps(b))

                for c in groupEntries:
                    groupItem = json.loads(json.dumps(c))
                    
                    if type(groupItem) is dict:
                        tempString = json.dumps(c)

                        try:
                            if (len(groupItem['organizationId'])) > 0:
                                encKey = BitwardenSecrets['OrgSecrets'][groupItem['organizationId']][0:32]
                                macKey = BitwardenSecrets['OrgSecrets'][groupItem['organizationId']][32:64]
                        except Exception:
                            encKey = BitwardenSecrets['GeneratedEncryptionKey']
                            macKey = BitwardenSecrets['GeneratedMACKey']

                        for match in regexPattern.findall(tempString):    
                            jsonEscapedString = json.JSONEncoder().encode(decryptCipherString(match, encKey, macKey))
                            jsonEscapedString = jsonEscapedString[1:(len(jsonEscapedString)-1)]
                            tempString = tempString.replace(match, jsonEscapedString)

                            # Get rid of the Bitwarden userId key/value pair.
                            userIdString = "\"userId\": \"" + datafile["userId"] + "\","
                            tempString = tempString.replace(userIdString, "")   

                        groupItemsList.append(json.loads(tempString))
                    
            decryptedEntries[group] = groupItemsList

    return(json.dumps(decryptedEntries, indent=2, ensure_ascii=False))


def main():
    if len(sys.argv) == 2:
        inputfile = sys.argv[1]
    else:
        inputfile = "data.json"

    decryptedJSON = decryptBitwardenJSON(inputfile)
    print(decryptedJSON)
    

if __name__ == "__main__":
          main()
