from pymongo import MongoClient
import os

MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')

client = MongoClient(MONGO_URI)

mongo_db = client[os.getenv('MONGO_DB', 'mydatabase')]