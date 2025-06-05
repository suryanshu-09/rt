#!/usr/bin/env python3
"""
Telegram Channel to Hugo Markdown Converter
Converts Telegram channel messages to Hugo-compatible markdown with proper frontmatter
"""

import credentials
import re
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict
import json

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
    DocumentAttributeFilename,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TelegramHugoExporter:
    def __init__(self, api_id: str, api_hash: str, phone_number: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone_number = phone_number
        self.client = TelegramClient("user_session", api_id, api_hash)

        # Create output directories
        self.output_dir = Path("hugo_content")
        self.content_dir = self.output_dir / "posts"

        for dir_path in [self.output_dir, self.content_dir]:
            dir_path.mkdir(exist_ok=True)

    async def start(self):
        """Start the client and authenticate as user"""
        await self.client.start(phone=self.phone_number)

        # Verify we're connected
        me = await self.client.get_me()
        logger.info(
            f"Connected as: {me.first_name} {me.last_name or ''} (@{me.username or 'no username'})"
        )

        return self.client

    async def export_channel(self, channel_identifier: str, limit: int = None) -> int:
        """Export all messages from a channel"""
        try:
            # Get the channel entity
            channel = await self.client.get_entity(channel_identifier)
            logger.info(f"Exporting messages from: {channel.title}")

            messages = []
            message_count = 0

            # Iterate through all messages
            async for message in self.client.iter_messages(channel, limit=limit):
                if message.text:  # Only process messages with text
                    messages.append(message)
                    message_count += 1

                    if message_count % 100 == 0:
                        logger.info(f"Collected {message_count} messages...")

            logger.info(f"Total messages collected: {len(messages)}")

            # Reverse to process in chronological order
            messages.reverse()

            # Group messages and convert to Hugo markdown
            await self.process_messages_to_hugo(messages, channel.title)

            return len(messages)

        except Exception as e:
            logger.error(f"Error exporting channel: {e}")
            raise

    async def process_messages_to_hugo(self, messages: List, channel_title: str):
        """Process messages and convert to Hugo markdown files"""
        grouped_messages = self.group_messages(messages)

        logger.info(f"Created {len(grouped_messages)} message groups")

        for i, message_group in enumerate(grouped_messages):
            try:
                logger.info(f"\n=== DEBUGGING GROUP {i + 1} ===")
                await self.debug_message_media(message_group)
                # In your process_messages_to_hugo method, add this line:
                await self.debug_album_structure(message_group)
                await self.create_hugo_post(message_group, channel_title, i)
                logger.info(f"Created Hugo post {i + 1}/{len(grouped_messages)}")
            except Exception as e:
                logger.error(f"Error creating Hugo post {i}: {e}")

    def group_messages(self, messages: List) -> List[List]:
        """Group messages based on continuation patterns like (1/3), (2/3), etc."""
        grouped = []
        current_group = []
        expecting_continuation = False
        expected_next = 1
        expected_total = 1

        for message in messages:
            if not message.text:
                continue

            text = message.text.strip()

            # Check if this is a continuation message - look for pattern at the end
            # Handle both (1/3) and 1/3 formats
            continuation_match = re.search(r"(?:\()?(\d+)/(\d+)(?:\))?\s*$", text)

            if continuation_match:
                current_num = int(continuation_match.group(1))
                total_num = int(continuation_match.group(2))

                logger.info(
                    f"Found continuation: {current_num}/{total_num} in message: {text[:100]}..."
                )

                if current_num == 1:
                    # Start of a new series
                    if current_group:
                        # Save previous group
                        grouped.append(current_group)
                        logger.info(
                            f"Saved previous group with {len(current_group)} messages"
                        )

                    # Start new group
                    current_group = [message]
                    if total_num > 1:
                        expecting_continuation = True
                        expected_next = 2
                        expected_total = total_num
                        logger.info(
                            f"Started new group, expecting {expected_next}/{expected_total}"
                        )
                    else:
                        # Single message series (1/1)
                        grouped.append(current_group)
                        current_group = []
                        expecting_continuation = False
                        logger.info("Completed single message series (1/1)")

                elif (
                    expecting_continuation
                    and current_num == expected_next
                    and total_num == expected_total
                ):
                    # Valid continuation
                    current_group.append(message)
                    expected_next += 1
                    logger.info(
                        f"Added continuation {current_num}/{total_num}, expecting {expected_next}"
                    )

                    if current_num == total_num:
                        # Series complete
                        grouped.append(current_group)
                        logger.info(
                            f"Completed group with {len(current_group)} messages"
                        )
                        current_group = []
                        expecting_continuation = False
                        expected_next = 1
                        expected_total = 1

                else:
                    # Unexpected continuation or broken sequence
                    logger.warning(
                        f"Unexpected continuation: got {current_num}/{total_num}, expected {expected_next}/{expected_total}"
                    )

                    if current_group:
                        grouped.append(current_group)
                        logger.info(
                            f"Saved incomplete group with {len(current_group)} messages"
                        )

                    if current_num == 1:
                        # Start new series
                        current_group = [message]
                        if total_num > 1:
                            expecting_continuation = True
                            expected_next = 2
                            expected_total = total_num
                        else:
                            grouped.append(current_group)
                            current_group = []
                            expecting_continuation = False
                    else:
                        # Treat as single message
                        grouped.append([message])
                        current_group = []
                        expecting_continuation = False
                        expected_next = 1
                        expected_total = 1
            else:
                # Single message (not part of a series)
                if current_group:
                    grouped.append(current_group)
                    logger.info(
                        f"Saved incomplete group with {len(current_group)} messages"
                    )
                    current_group = []

                grouped.append([message])
                logger.info("Added single message")
                expecting_continuation = False
                expected_next = 1
                expected_total = 1

        # Add any remaining group
        if current_group:
            grouped.append(current_group)
            logger.info(f"Saved final group with {len(current_group)} messages")

        logger.info(f"Grouped {len(messages)} messages into {len(grouped)} groups")
        return grouped

    def parse_message_structure(self, text: str) -> Dict:
        """Parse the message structure to extract party name, committee, etc."""
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        structure = {
            "party_name": "",
            "committee": "",
            "press_release": False,
            "date": "",
            "title": "",
            "content_lines": [],
        }

        content_started = False

        for i, line in enumerate(lines):
            line = line.strip()

            if not line:
                continue

            # Check for party name patterns
            if not structure["party_name"] and not content_started:
                # Look for lines that contain party indicators
                party_indicators = [
                    "party",
                    "movement",
                    "organization",
                    "front",
                    "union",
                    "congress",
                    "league",
                    "association",
                    "council",
                    "committee",
                ]

                if any(indicator in line.lower() for indicator in party_indicators):
                    structure["party_name"] = line
                    continue

                # Also check if line looks like an organization name (title case, multiple words)
                words = line.split()
                if len(words) >= 2 and all(word[0].isupper() for word in words if word):
                    structure["party_name"] = line
                    continue

            # Check for committee/division info
            if not structure["committee"] and not content_started:
                committee_indicators = [
                    "committee",
                    "central",
                    "zonal",
                    "provincial",
                    "district",
                    "division",
                    "wing",
                    "cell",
                    "bureau",
                    "secretariat",
                ]

                if any(indicator in line.lower() for indicator in committee_indicators):
                    structure["committee"] = line
                    continue

            # Check for Press Release
            if "press release" in line.lower():
                structure["press_release"] = True
                continue

            # Check for date (format: Date: DD-MM-YYYY or just DD-MM-YYYY)
            if line.lower().startswith("date:") or re.match(r"\d{2}-\d{2}-\d{4}", line):
                date_match = re.search(r"(\d{2}-\d{2}-\d{4})", line)
                if date_match:
                    structure["date"] = date_match.group(1)
                continue

            # Everything else goes to content
            structure["content_lines"].append(line)
            content_started = True

        # Extract title from first content line if not set
        if structure["content_lines"] and not structure["title"]:
            structure["title"] = structure["content_lines"][0]
            if len(structure["title"]) > 100:
                structure["title"] = structure["title"][:100] + "..."

        return structure

    def generate_folder_name(self, text: str, date: str = None) -> str:
        """Generate a safe folder name from the message content"""
        if not text:
            return f"post_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Get first sentence or first 50 characters
        first_line = text.split("\n")[0].strip()
        if len(first_line) > 50:
            first_line = first_line[:50]

        # Clean folder name
        folder_name = re.sub(r"[^\w\s-]", "", first_line)
        folder_name = re.sub(r"[-\s]+", "-", folder_name)
        folder_name = folder_name.strip("-").lower()

        if not folder_name:
            folder_name = f"post_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Add date prefix if available
        if date:
            try:
                date_obj = datetime.strptime(date, "%d-%m-%Y")
                date_prefix = date_obj.strftime("%Y-%m-%d")
                folder_name = f"{date_prefix}-{folder_name}"
            except:
                pass

        return folder_name

    def convert_date_format(self, date_str: str) -> str:
        """Convert date from DD-MM-YYYY to YYYY-MM-DD for Hugo"""
        try:
            date_obj = datetime.strptime(date_str, "%d-%m-%Y")
            return date_obj.strftime("%Y-%m-%d")
        except:
            return datetime.now().strftime("%Y-%m-%d")

    async def create_hugo_post(
        self, message_group: List, channel_title: str, group_index: int
    ):
        """Create a Hugo post from a group of messages"""
        if not message_group:
            return

        # Combine all message texts
        combined_text = ""
        video_links = []
        has_media = False

        logger.info(f"Processing group with {len(message_group)} messages")

        for i, message in enumerate(message_group):
            logger.debug(f"Message {i + 1}: {message.text[:50]}...")

            # Remove continuation markers from individual messages before combining
            # Handle both (1/3) and 1/3 formats
            message_text = re.sub(
                r"(?:\()?(\d+)/(\d+)(?:\))?\s*$", "", message.text.strip()
            )
            combined_text += message_text + "\n\n"

            # Check for media
            if message.media:
                has_media = True
                if isinstance(message.media, MessageMediaDocument):
                    document = message.media.document
                    is_video = any(
                        isinstance(attr, DocumentAttributeVideo)
                        for attr in document.attributes
                    )
                    if is_video:
                        # Get video link from Telegram
                        video_link = f"https://t.me/{channel_title.replace('@', '')}/{message.id}"
                        video_links.append(video_link)

        # Clean up combined text
        combined_text = combined_text.strip()

        # Parse message structure
        structure = self.parse_message_structure(combined_text)

        # Clean up party name and other fields by removing random asterisks
        if structure["party_name"]:
            structure["party_name"] = re.sub(
                r"\*+", "", structure["party_name"]
            ).strip()
        if structure["committee"]:
            structure["committee"] = re.sub(r"\*+", "", structure["committee"]).strip()

        # Generate folder name
        folder_name = self.generate_folder_name(combined_text, structure["date"])
        post_dir = self.content_dir / folder_name
        post_dir.mkdir(exist_ok=True)

        # Download media and get featured image (album-aware)
        featured_image = None
        if has_media:
            featured_image = await self.download_album_media(
                message_group, folder_name, post_dir
            )

        # Create Hugo frontmatter
        hugo_date = (
            self.convert_date_format(structure["date"])
            if structure["date"]
            else datetime.now().strftime("%Y-%m-%d")
        )

        # Use the first content line as title, or generate one
        title = structure["title"] if structure["title"] else "Press Release"
        if len(title) > 30:
            title = title[:70].rstrip() + "..."

        frontmatter = f"""+++
date = '{hugo_date}'
draft = false
title = "{title.replace('"', '\\"')}"
authors = ['{structure["party_name"]}']"""

        # Add featured image if available
        # if featured_image:
        #     frontmatter += f'\nfeatured_image = "{featured_image}"'

        frontmatter += "\n+++\n\n"

        # Create content with proper HTML structure
        content = []

        # Add video links at the top if any
        if video_links:
            content.append("<h2>Videos</h2>")
            for video_link in video_links:
                content.append(f"[📹 Watch Video]({video_link})")
            content.append("")

        # Add party name as H1
        if structure["party_name"]:
            content.append(f"<h1>{structure['party_name']}</h1>")

        # Add committee as H2
        if structure["committee"]:
            content.append(f"<h2>{structure['committee']}</h2>")

        # Add Press Release as H3
        if structure["press_release"]:
            content.append("<h3>Press Release</h3>")

        # Add date if available
        if structure["date"]:
            content.append(f"Date: {structure['date']}")
            content.append("")  # Add blank line after date

        # Add the first content line as H2 (main statement/title)
        if structure["content_lines"]:
            # Clean the first line and use as main heading
            main_heading = re.sub(r"\*+", "", structure["content_lines"][0]).strip()
            content.append(f"<h2>{main_heading}</h2>")
            content.append("")  # Add blank line after heading

            # Rest of the content as plain text, preserving paragraph and line breaks
            if len(structure["content_lines"]) > 1:
                content_lines = structure["content_lines"][1:]
                for line in content_lines:
                    cleaned_line = re.sub(r"\*+", "", line.rstrip())
                    content.append(cleaned_line)
                    content.append("")  # Ensure file ends with newline

        # After writing the content list but before writing to file
        # Add gallery of images if available
        for image_path in sorted(post_dir.glob("image-*-*.jpg")):
            content.append(f"![Image]({image_path.name})")
            content.append("")
        # Write the Hugo post
        post_content = frontmatter + "\n".join(content)

        index_file = post_dir / "index.md"
        with open(index_file, "w", encoding="utf-8") as f:
            f.write(post_content)

        logger.info(f"Created Hugo post: {post_dir}")

    async def download_album_media(
        self, message_group: List, folder_name: str, post_dir: Path
    ) -> Optional[str]:
        """Download all media from a message group (including albums and single messages with multiple media). Returns first image as featured."""
        featured_image = None
        image_count = 0

        # Clean folder name for file naming
        clean_folder_name = re.sub(r"[^\w\-]", "-", folder_name.lower())
        clean_folder_name = re.sub(r"-+", "-", clean_folder_name).strip("-")

        # Sort messages by ID to ensure consistent ordering
        message_group = sorted(message_group, key=lambda x: x.id)

        # Group messages by grouped_id for album handling
        album_groups = {}
        single_messages = []

        for message in message_group:
            if not message.media:
                continue

            if hasattr(message, "grouped_id") and message.grouped_id:
                if message.grouped_id not in album_groups:
                    album_groups[message.grouped_id] = []
                album_groups[message.grouped_id].append(message)
            else:
                single_messages.append(message)

        # Process album groups first
        for grouped_id, album_messages in album_groups.items():
            logger.info(
                f"Processing album with grouped_id: {grouped_id}, {len(album_messages)} messages"
            )

            # Sort album messages by message ID
            album_messages.sort(key=lambda x: x.id)

            for message in album_messages:
                try:
                    # Photo
                    if isinstance(message.media, MessageMediaPhoto):
                        image_count += 1

                        if image_count == 1:
                            # First image is the featured image
                            filename = f"featured-{clean_folder_name}.jpg"
                        else:
                            # Subsequent images
                            filename = f"image-{image_count}-{clean_folder_name}.jpg"

                        file_path = post_dir / filename
                        await message.download_media(file=str(file_path))

                        if not featured_image:
                            featured_image = filename

                        logger.info(f"Downloaded album photo: {filename}")

                    # Document (could be video, image, or other file)
                    elif isinstance(message.media, MessageMediaDocument):
                        document = message.media.document
                        mime_type = document.mime_type or ""

                        # Try to extract original filename
                        original_filename = None
                        for attr in document.attributes:
                            if isinstance(attr, DocumentAttributeFilename):
                                original_filename = attr.file_name
                                break

                        # Determine file type and generate appropriate filename
                        if "image" in mime_type:
                            image_count += 1

                            # Get file extension from mime type or original filename
                            if original_filename:
                                ext = Path(original_filename).suffix or ".jpg"
                            else:
                                ext_map = {
                                    "image/jpeg": ".jpg",
                                    "image/png": ".png",
                                    "image/gif": ".gif",
                                    "image/webp": ".webp",
                                    "image/bmp": ".bmp",
                                }
                                ext = ext_map.get(mime_type, ".jpg")

                            if image_count == 1:
                                # First image is the featured image
                                filename = f"featured-{clean_folder_name}{ext}"
                            else:
                                # Subsequent images
                                filename = (
                                    f"image-{image_count}-{clean_folder_name}{ext}"
                                )

                            if not featured_image:
                                featured_image = filename

                            file_path = post_dir / filename
                            await message.download_media(file=str(file_path))
                            logger.info(f"Downloaded album image document: {filename}")

                        elif "video" in mime_type:
                            # Handle videos
                            if original_filename:
                                base_name = Path(original_filename).stem
                                ext = Path(original_filename).suffix or ".mp4"
                            else:
                                base_name = f"video-{message.id}"
                                ext = ".mp4"

                            filename = f"{base_name}-{clean_folder_name}{ext}"
                            file_path = post_dir / filename
                            await message.download_media(file=str(file_path))
                            logger.info(f"Downloaded album video: {filename}")

                        else:
                            # Handle other document types
                            if original_filename:
                                base_name = Path(original_filename).stem
                                ext = Path(original_filename).suffix or ".bin"
                            else:
                                base_name = f"document-{message.id}"
                                ext = ".bin"

                            filename = f"{base_name}-{clean_folder_name}{ext}"
                            file_path = post_dir / filename
                            await message.download_media(file=str(file_path))
                            logger.info(f"Downloaded album document: {filename}")

                except Exception as e:
                    logger.error(
                        f"Error downloading media from album message {message.id}: {e}"
                    )
                    continue

        # Process single messages (not part of albums)
        for message in single_messages:
            logger.info(f"Processing single message with media: {message.id}")

            try:
                # Photo
                if isinstance(message.media, MessageMediaPhoto):
                    image_count += 1

                    if image_count == 1:
                        # First image is the featured image
                        filename = f"featured-{clean_folder_name}.jpg"
                    else:
                        # Subsequent images
                        filename = f"image-{image_count}-{clean_folder_name}.jpg"

                    file_path = post_dir / filename
                    await message.download_media(file=str(file_path))

                    if not featured_image:
                        featured_image = filename

                    logger.info(f"Downloaded photo: {filename}")

                # Document (could be video, image, or other file)
                elif isinstance(message.media, MessageMediaDocument):
                    document = message.media.document
                    mime_type = document.mime_type or ""

                    # Try to extract original filename
                    original_filename = None
                    for attr in document.attributes:
                        if isinstance(attr, DocumentAttributeFilename):
                            original_filename = attr.file_name
                            break

                    # Determine file type and generate appropriate filename
                    if "image" in mime_type:
                        image_count += 1

                        # Get file extension from mime type or original filename
                        if original_filename:
                            ext = Path(original_filename).suffix or ".jpg"
                        else:
                            ext_map = {
                                "image/jpeg": ".jpg",
                                "image/png": ".png",
                                "image/gif": ".gif",
                                "image/webp": ".webp",
                                "image/bmp": ".bmp",
                            }
                            ext = ext_map.get(mime_type, ".jpg")

                        if image_count == 1:
                            # First image is the featured image
                            filename = f"featured-{clean_folder_name}{ext}"
                        else:
                            # Subsequent images
                            filename = f"image-{image_count}-{clean_folder_name}{ext}"

                        if not featured_image:
                            featured_image = filename

                        file_path = post_dir / filename
                        await message.download_media(file=str(file_path))
                        logger.info(f"Downloaded image document: {filename}")

                    elif "video" in mime_type:
                        # Handle videos
                        if original_filename:
                            base_name = Path(original_filename).stem
                            ext = Path(original_filename).suffix or ".mp4"
                        else:
                            base_name = f"video-{message.id}"
                            ext = ".mp4"

                        filename = f"{base_name}-{clean_folder_name}{ext}"
                        file_path = post_dir / filename
                        await message.download_media(file=str(file_path))
                        logger.info(f"Downloaded video: {filename}")

                    else:
                        # Handle other document types
                        if original_filename:
                            base_name = Path(original_filename).stem
                            ext = Path(original_filename).suffix or ".bin"
                        else:
                            base_name = f"document-{message.id}"
                            ext = ".bin"

                        filename = f"{base_name}-{clean_folder_name}{ext}"
                        file_path = post_dir / filename
                        await message.download_media(file=str(file_path))
                        logger.info(f"Downloaded document: {filename}")

                # Handle other media types
                else:
                    logger.info(
                        f"Attempting to download other media type: {type(message.media)}"
                    )
                    try:
                        # Try to download any other media type
                        filename = f"media-{message.id}-{clean_folder_name}"
                        file_path = post_dir / filename
                        downloaded_path = await message.download_media(
                            file=str(file_path)
                        )

                        if downloaded_path:
                            actual_filename = Path(downloaded_path).name
                            logger.info(f"Downloaded other media: {actual_filename}")

                            # Check if it's an image based on file extension
                            img_extensions = {
                                ".jpg",
                                ".jpeg",
                                ".png",
                                ".gif",
                                ".webp",
                                ".bmp",
                            }
                            if Path(actual_filename).suffix.lower() in img_extensions:
                                image_count += 1
                                if not featured_image:
                                    featured_image = actual_filename
                    except Exception as e:
                        logger.warning(f"Could not download other media type: {e}")

            except Exception as e:
                logger.error(f"Error downloading media from message {message.id}: {e}")
                continue

        if featured_image:
            logger.info(f"Featured image set to: {featured_image}")
            logger.info(f"Total images downloaded: {image_count}")
        else:
            logger.info("No images found for featured image")

        return featured_image

    async def debug_album_structure(self, message_group: List):
        """Debug method to understand album structure"""
        logger.info(f"\n=== ALBUM STRUCTURE DEBUG ===")
        logger.info(f"Message group has {len(message_group)} messages")

        # Group by grouped_id
        album_groups = {}
        single_messages = []

        for message in message_group:
            logger.info(f"Message ID: {message.id}")
            logger.info(f"  - Has media: {message.media is not None}")
            logger.info(f"  - Grouped ID: {getattr(message, 'grouped_id', 'None')}")
            logger.info(
                f"  - Text preview: {message.text[:50] if message.text else 'No text'}..."
            )

            if message.media:
                if isinstance(message.media, MessageMediaPhoto):
                    logger.info(f"  - Media: Photo (ID: {message.media.photo.id})")
                elif isinstance(message.media, MessageMediaDocument):
                    logger.info(
                        f"  - Media: Document (ID: {message.media.document.id}, type: {message.media.document.mime_type})"
                    )

            if hasattr(message, "grouped_id") and message.grouped_id:
                if message.grouped_id not in album_groups:
                    album_groups[message.grouped_id] = []
                album_groups[message.grouped_id].append(message)
            else:
                single_messages.append(message)

            logger.info("")

        logger.info(f"Found {len(album_groups)} album groups:")
        for grouped_id, messages in album_groups.items():
            logger.info(f"  - Album {grouped_id}: {len(messages)} messages")
            for msg in messages:
                logger.info(
                    f"    * Message {msg.id}: {'Photo' if isinstance(msg.media, MessageMediaPhoto) else 'Document' if isinstance(msg.media, MessageMediaDocument) else 'Other'}"
                )

        logger.info(f"Found {len(single_messages)} single messages with media")
        logger.info("=" * 50)

    async def debug_message_media(self, message_group: List):
        """Debug method to analyze what type of media we're dealing with"""

        for i, message in enumerate(message_group):
            logger.info(f"\n=== DEBUG MESSAGE {i + 1} (ID: {message.id}) ===")
            logger.info(
                f"Message text preview: {message.text[:100] if message.text else 'No text'}"
            )
            logger.info(f"Has media: {message.media is not None}")
            logger.info(f"Grouped ID: {getattr(message, 'grouped_id', 'None')}")

            if message.media:
                logger.info(f"Media type: {type(message.media)}")
                logger.info(f"Media class name: {message.media.__class__.__name__}")

                # Detailed analysis based on media type
                if isinstance(message.media, MessageMediaPhoto):
                    logger.info(f"Photo ID: {message.media.photo.id}")
                    logger.info(
                        f"Photo has sizes: {len(message.media.photo.sizes) if hasattr(message.media.photo, 'sizes') else 'Unknown'}"
                    )

                elif isinstance(message.media, MessageMediaDocument):
                    document = message.media.document
                    logger.info(f"Document ID: {document.id}")
                    logger.info(f"Document mime_type: {document.mime_type}")
                    logger.info(f"Document size: {document.size}")

                    # Check attributes
                    for attr in document.attributes:
                        logger.info(f"Document attribute: {type(attr).__name__}")
                        if isinstance(attr, DocumentAttributeFilename):
                            logger.info(f"  - Filename: {attr.file_name}")
                        elif isinstance(attr, DocumentAttributeVideo):
                            logger.info(
                                f"  - Video: {attr.w}x{attr.h}, duration: {attr.duration}s"
                            )

                else:
                    logger.info(f"Other media type details: {dir(message.media)}")

                    # Try to get any downloadable content
                    try:
                        # Check if media has any downloadable attributes
                        if hasattr(message.media, "document"):
                            logger.info(f"Media has document: {message.media.document}")
                        if hasattr(message.media, "photo"):
                            logger.info(f"Media has photo: {message.media.photo}")
                        if hasattr(message.media, "webpage"):
                            logger.info(f"Media has webpage: {message.media.webpage}")
                    except Exception as e:
                        logger.info(f"Error examining media attributes: {e}")

            # Try to download and see what happens
            if message.media:
                try:
                    logger.info("Attempting test download...")
                    temp_path = Path(f"debug_download_{message.id}")
                    downloaded = await message.download_media(file=str(temp_path))
                    if downloaded:
                        logger.info(f"Successfully downloaded to: {downloaded}")
                        # Clean up
                        if Path(downloaded).exists():
                            Path(downloaded).unlink()
                    else:
                        logger.info("Download returned None")
                except Exception as e:
                    logger.info(f"Download failed: {e}")

            logger.info("=" * 50)


# Add this method to your TelegramHugoExporter class and call it before processing
# You can call it like this in your process_messages_to_hugo method:
# await self.debug_message_media(message_group)


async def main():
    """Main function to run the exporter"""
    # Configuration using credentials file
    API_ID = credentials.api_id
    API_HASH = credentials.api_hash
    PHONE_NUMBER = credentials.phone_number

    # Create the exporter
    exporter = TelegramHugoExporter(API_ID, API_HASH, PHONE_NUMBER)

    try:
        # Start the client (will ask for verification code on first run)
        await exporter.start()

        # Example: Export a channel
        channel_to_export = input(
            "Enter channel username (e.g., @channelname) or channel ID: "
        ).strip()

        if not channel_to_export:
            print("No channel specified!")
            return

        print(f"Starting Hugo export of {channel_to_export}...")

        # Optional: limit number of messages (remove limit=None to get all messages)
        message_count = await exporter.export_channel(channel_to_export, limit=None)

        print(f"Hugo export completed! Processed {message_count} messages.")
        print(f"Hugo content saved in: {exporter.content_dir}")
        print("\nContent structure:")
        print("hugo_content/")
        print("└── posts/")
        print("    ├── 2024-10-12-condemn-the-illegal-extra-judicial/")
        print("    │   ├── index.md")
        print("    │   └── featured-condemn-the-illegal-extra-judicial.png")
        print("    └── ...")

    except KeyboardInterrupt:
        logger.info("Export stopped by user")
    except Exception as e:
        logger.error(f"Export error: {e}")
    finally:
        await exporter.client.disconnect()


if __name__ == "__main__":
    # Install required packages:
    # pip install telethon

    print("Telegram Channel to Hugo Converter")
    print("==================================")
    asyncio.run(main())
