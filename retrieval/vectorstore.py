from sentence_transformers import SentenceTransformer
from ingestion.nodes import chunks
import chromadb
from chromadb.utils import embedding_functions
from langchain_core.tools import tool

# ============================================================
# CHROMA SETUP (persisted collection, re-embedded from ingestion chunks on import)
# ============================================================

embedding_fn=embedding_functions.SentenceTransformerEmbeddingFunction(
    model_name="sentence-transformers/all-MiniLM-L6-v2"
)

client=chromadb.PersistentClient(path="./my_chroma_db")
collection=client.get_or_create_collection(name="usda_qcommerce",
                                           embedding_function=embedding_fn)

texts=[]
ids=[]
metadata=[]

for chunk in chunks:
    texts.append(chunk["text"])

for i in range(len(chunks)):
    id=f"chunk_{i}"
    ids.append(id)

for chunk in chunks:
    metadata.append({"page":chunk["page"]})

# re-adding the same ids on every import just overwrites the existing entries, so this is safe to re-run
collection.add(documents=texts,ids=ids,metadatas=metadata)


# ============================================================
# RETRIEVAL TOOL (exposed to the prosecutor agent)
# ============================================================

@tool
def retrieve_top_chunks(query:str):

    """Search the uploaded document for text relevant to a query. Use this to find supporting or contradicting evidence from the source document."""

    result=collection.query(
        query_texts=[query],
        n_results=3,

    )

    chunk_found=[]

    for text,meta in zip(result["documents"][0],result["metadatas"][0]):
        chunk_found.append({"page":meta["page"],
                            "text":text})

    return chunk_found


# quick manual check: run this file directly to sanity-test retrieval
if __name__ == "__main__":
    results = retrieve_top_chunks("online grocery market growth")
    for r in results:
        print(f"Page {r['page']}: {r['text'][:150]}...")
        print("-" * 40)