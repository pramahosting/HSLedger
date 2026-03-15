from cassandra.cluster import Cluster, Session

cluster = Cluster(['127.0.0.1'], port=9042)

print(cluster.connect('hsledger'))