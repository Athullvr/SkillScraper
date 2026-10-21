type ProfileProps = {
  name: string;
  imageUrl: string;
};

export function Profile({ name, imageUrl }: ProfileProps) {
  return <img src={imageUrl} alt={`${name} profile`} />;
}
