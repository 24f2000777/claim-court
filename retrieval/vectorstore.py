from sentence_transformers import SentenceTransformer
from ingestion.nodes import chunks as default_chunks
import chromadb
from chromadb.utils import embedding_functions
from langchain_core.tools import tool

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

    new_collection = client.get_or_create_collection(name=collection_name, embedding_function=embedding_fn)

    texts = [chunk["text"] for chunk in chunks]
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    metadata = [{"page": chunk["page"]} for chunk in chunks]

    new_collection.add(documents=texts, ids=ids, metadatas=metadata)
    return new_collection


# default collection, built from the bundled USDA document at import time (backward compatible)
collection = build_vectorstore(default_chunks, collection_name="usda_qcommerce")


def set_active_document(chunks):
    """Rebuilds the vectorstore from a new document's chunks and points retrieve_top_chunks at it.
    Called when the user uploads a new PDF in the UI."""
    global collection
    collection = build_vectorstore(chunks, collection_name="uploaded_doc")


# ============================================================
# RETRIEVAL TOOL (exposed to the prosecutor agent)
# ============================================================

@tool
def retrieve_top_chunks(query: str):
    """Search the active document for text relevant to a query. Use this to find supporting or contradicting evidence from the source document."""
    result = collection.query(query_texts=[query], n_results=3)

    chunk_found = []
    for text, meta in zip(result["documents"][0], result["metadatas"][0]):
        chunk_found.append({"page": meta["page"], "text": text})

    return chunk_found


if __name__ == "__main__":
    results = retrieve_top_chunks("online grocery market growth")
    for r in results:
        print(f"Page {r['page']}: {r['text'][:150]}...")
        print("-" * 40)