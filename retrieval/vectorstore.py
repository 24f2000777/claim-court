import time
import uuid

import chromadb
from chromadb.utils import embedding_functions

from ingestion.nodes import chunks as default_chunks

# ============================================================
# CHROMA SETUP (persisted client; collections are built per-document)
# ============================================================

embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

client = chromadb.PersistentClient(path="./my_chroma_db")


def build_vectorstore(chunks, collection_name):
    """Creates (or overwrites) a Chroma collection from the given chunks."""
    try:
        client.delete_collection(name=collection_name)
    except Exception:
        pass  # collection didn't exist yet, nothing to delete

    new_collection = client.get_or_create_collection(
        name=collection_name, embedding_function=embedding_fn, metadata={"created": time.time()}
    )

    texts = [chunk["text"] for chunk in chunks]
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    metadata = [{"page": chunk["page"]} for chunk in chunks]

    new_collection.add(documents=texts, ids=ids, metadatas=metadata)
    return new_collection


# bundled USDA document, built at import time and used when nothing has been uploaded
DEFAULT_COLLECTION = "usda_qcommerce"
build_vectorstore(default_chunks, collection_name=DEFAULT_COLLECTION)

MAX_UPLOADED_COLLECTIONS = 20  # oldest uploads are dropped so a shared deployment does not grow forever


def add_uploaded_document(chunks):
    """Indexes an uploaded document in its own collection and returns the collection name.
    Each upload gets a separate collection, so visitors never overwrite each other's documents."""
    name = f"uploaded_{uuid.uuid4().hex[:12]}"
    build_vectorstore(chunks, collection_name=name)

    uploaded = [c for c in client.list_collections() if c.name.startswith("uploaded_")]
    uploaded.sort(key=lambda c: (c.metadata or {}).get("created", 0))
    for old in uploaded[:-MAX_UPLOADED_COLLECTIONS]:
        client.delete_collection(name=old.name)
    return name


# ============================================================
# RETRIEVAL
# ============================================================

def retrieve_top_chunks(query: str, collection_name: str = DEFAULT_COLLECTION):
    """Returns the 3 chunks of the named document collection that best match the query."""
    collection = client.get_collection(name=collection_name, embedding_function=embedding_fn)
    result = collection.query(query_texts=[query], n_results=3)

    return [
        {"page": meta["page"], "text": text}
        for text, meta in zip(result["documents"][0], result["metadatas"][0])
    ]


if __name__ == "__main__":
    results = retrieve_top_chunks("online grocery market growth")
    for r in results:
        print(f"Page {r['page']}: {r['text'][:150]}...")
        print("-" * 40)