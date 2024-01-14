from typing import Optional, Dict
from common.models import Collection, SortOrderEnum, ListResult, TextSplitter, ModelType
from common.database_ops import collection as db_collection
from common.error import ErrorCode, raise_http_error
from common.services.model.model import get_model

__all__ = [
    "list_collections",
    "create_collection",
    "update_collection",
    "get_collection",
    "delete_collection",
]


async def validate_and_get_collection(postgres_conn, collection_id: str) -> Collection:
    collection = await db_collection.get_collection(postgres_conn, collection_id)
    if not collection:
        raise_http_error(ErrorCode.OBJECT_NOT_FOUND, message=f"Collection {collection_id} not found.")
    return collection


async def list_collections(
    postgres_conn,
    limit: int,
    order: SortOrderEnum,
    after: Optional[str],
    before: Optional[str],
    offset: Optional[int],
    id_search: Optional[str],
    name_search: Optional[str],
) -> ListResult:
    """
    List collections
    :param postgres_conn: postgres connection
    :param limit: the limit of the query
    :param order: the order of the query, asc or desc
    :param after: the cursor ID to query after
    :param before: the cursor ID to query before
    :param offset: the offset of the query
    :param id_search: the collection ID to search for
    :param name_search: the collection name to search for
    :return: a list of collections, total count of collections, and whether there are more collections
    """

    after_collection, before_collection = None, None

    if after:
        after_collection = await validate_and_get_collection(postgres_conn, after)

    if before:
        before_collection = await validate_and_get_collection(postgres_conn, before)

    return await db_collection.list_collections(
        postgres_conn=postgres_conn,
        limit=limit,
        order=order,
        after_collection=after_collection,
        before_collection=before_collection,
        offset=offset,
        prefix_filters={
            "collection_id": id_search,
            "name": name_search,
        },
    )


async def create_collection(
    postgres_conn,
    name: str,
    description: str,
    capacity: int,
    embedding_model_id: str,
    text_splitter: TextSplitter,
    metadata: Dict[str, str],
) -> Collection:
    """
    Create collection
    :param postgres_conn: postgres connection
    :param name: the collection name
    :param description: the collection description
    :param capacity: the collection capacity
    :param embedding_model_id: the embedding model id
    :param text_splitter: the text splitter
    :param metadata: the collection metadata
    :return: the created collection
    """

    # validate embedding model
    embedding_model = await get_model(postgres_conn, embedding_model_id)
    model_schema = embedding_model.model_schema()
    if not model_schema.type == ModelType.TEXT_EMBEDDING:
        raise_http_error(
            ErrorCode.REQUEST_VALIDATION_ERROR,
            message=f"Model {embedding_model_id} is not a valid embedding model.",
        )

    # validate embedding size
    embedding_size = embedding_model.model_schema().properties.get("embedding_size")
    if not embedding_size:
        raise_http_error(
            ErrorCode.REQUEST_VALIDATION_ERROR,
            message=f"Embedding model {embedding_model_id} has an invalid embedding size.",
        )

    # create collection
    collection = await db_collection.create_collection(
        postgres_conn=postgres_conn,
        name=name,
        description=description,
        capacity=capacity,
        embedding_model_id=embedding_model_id,
        embedding_size=embedding_size,
        text_splitter=text_splitter,
        metadata=metadata,
    )
    return collection


async def update_collection(
    postgres_conn,
    collection_id: str,
    name: Optional[str],
    description: Optional[str],
    metadata: Optional[Dict[str, str]],
) -> Collection:
    """
    Update collection
    :param postgres_conn: postgres connection
    :param collection_id: the collection id
    :param name: the collection name to update
    :param description: the collection description to update
    :param metadata: the collection metadata to update
    :return: the updated collection
    """

    collection: Collection = await validate_and_get_collection(postgres_conn, collection_id=collection_id)

    update_dict = {}

    if name:
        update_dict["name"] = name
    if description:
        update_dict["description"] = description
    if metadata:
        update_dict["metadata"] = metadata

    if update_dict:
        collection = await db_collection.update_collection(
            conn=postgres_conn,
            collection=collection,
            update_dict=update_dict,
        )

    return collection


async def get_collection(postgres_conn, collection_id: str) -> Collection:
    """
    Get collection
    :param postgres_conn: postgres connection
    :param collection_id: the collection id
    :return: the collection
    """

    collection: Collection = await validate_and_get_collection(postgres_conn, collection_id)
    return collection


async def delete_collection(postgres_conn, collection_id: str) -> None:
    """
    Delete collection
    :param postgres_conn: postgres connection
    :param collection_id: the collection id
    """

    collection: Collection = await validate_and_get_collection(postgres_conn, collection_id)
    await db_collection.delete_collection(postgres_conn, collection)
