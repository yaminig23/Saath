/* Read a user photo file into a data URL (shared by Teach and Videos). */

export function readPhoto(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (file.size > 4 * 1024 * 1024) {
      reject(new Error("Photo is too large (4 MB max)."));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : "");
    reader.onerror = () => reject(new Error("Could not read that photo."));
    reader.readAsDataURL(file);
  });
}
